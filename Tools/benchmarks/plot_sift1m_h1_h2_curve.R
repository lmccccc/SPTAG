#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (!length(args) %in% c(2, 3, 4, 5) ||
    (length(args) == 5 && args[[5]] != "--partial-scenarios")) {
  stop(paste(
    "usage: plot_sift1m_h1_h2_curve.R <results.jsonl> <output-prefix>",
    "[paired-results.summary.csv] [supplier.summary.json] [--partial-scenarios]"
  ))
}

suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
})

input <- args[[1]]
output <- args[[2]]
rows <- stream_in(file(input), verbose = FALSE)
if (nrow(rows) == 0) {
  stop("benchmark result file is empty")
}

has_fixed_hierarchy <- any(rows$mode == "H3HierarchyFixed")
has_routing_only <- "hierarchy_design" %in% names(rows) &&
  any(rows$hierarchy_design == "routing_only", na.rm = TRUE)
has_corrected_placement <- "placement_policy" %in% names(rows) &&
  any(rows$placement_policy == "merge_score_retain", na.rm = TRUE)
if (has_fixed_hierarchy) {
  rows <- rows[rows$mode != "H3Hierarchy", , drop = FALSE]
  rows$mode[rows$mode == "H3HierarchyFixed"] <- "H3Hierarchy"
}

has_paired <- length(args) >= 3
has_supplier <- length(args) >= 4
allow_partial_scenarios <- length(args) == 5
partial_scenarios <- FALSE
has_sweep <- FALSE
if (has_paired) {
  paired <- read.csv(args[[3]], stringsAsFactors = FALSE)
  metrics <- c("qps_median", "qps_min", "qps_max", "recall_median")
  required <- c("workload", "nprobe", paste0("old_", metrics), paste0("new_", metrics))
  if (!all(required %in% names(paired)) || nrow(paired) != 20 ||
      anyDuplicated(paired[c("workload", "nprobe")]) ||
      !setequal(paired$workload, c(
        "unfilter", "broad_tag", "medium_tag", "sparse_tag", "mixed_dnf"
      )) ||
      any(table(paired$workload, paired$nprobe) != 1) ||
      !setequal(paired$nprobe, c(32, 64, 128, 256))) {
    stop("paired summary must contain five workloads at probes 32/64/128/256")
  }
  plot_columns <- c(
    "mode", "workload", "nprobe", "recall", "qps", "qps_trial_count",
    "qps_trial_min", "qps_trial_max", "graph_signature_pruning", "top_graph_maxcheck"
  )
  for (column in setdiff(plot_columns, names(rows))) rows[[column]] <- NA
  rows <- rows[plot_columns]
  for (side in c("old", "new")) {
    values <- paired[paste0(side, "_", metrics)]
    if (any(!is.finite(as.matrix(values))) ||
        any(values[[1]] <= 0 | values[[2]] <= 0 |
            values[[2]] > values[[1]] | values[[1]] > values[[3]]) ||
        any(values[[4]] < 0 | values[[4]] > 1)) {
      stop("invalid paired recall or QPS range")
    }
    added <- data.frame(
      mode = if (side == "old") "H3PairedOld" else "H3Current",
      workload = paired$workload, nprobe = paired$nprobe,
      recall = values[[4]], qps = values[[1]], qps_trial_count = 3,
      qps_trial_min = values[[2]], qps_trial_max = values[[3]],
      graph_signature_pruning = FALSE, top_graph_maxcheck = 512
    )
    rows <- rbind(rows, added[plot_columns])
  }
}

current_modes <- c(
  h1 = "H1Reference", h3 = "H3Reference",
  control = "GraphControl", supplier = "PostingSupplier"
)
if (has_supplier) {
  current <- fromJSON(args[[4]])
  required <- c(
    "scenario", "case", "recall_at_10", "qps", "ordinary_ms", "ordinary_ms_runs"
  )
  scenarios <- c("unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf")
  if (!is.data.frame(current) || !all(required %in% names(current))) {
    stop("supplier summary is missing required measurement fields")
  }
  if (!"nprobe" %in% names(current)) current$nprobe <- 24
  if (!is.numeric(current$nprobe) || any(!is.finite(current$nprobe)) ||
      any(current$nprobe < 1 | current$nprobe != floor(current$nprobe))) {
    stop("current nprobe values must be positive integers")
  }
  current_cases <- unique(current$case)
  current_scenarios <- scenarios[scenarios %in% current$scenario]
  missing_current_scenarios <- setdiff(scenarios, current_scenarios)
  partial_scenarios <- length(missing_current_scenarios) > 0
  if (anyDuplicated(current[c("scenario", "case", "nprobe")]) ||
      !all(current$scenario %in% scenarios) ||
      (!allow_partial_scenarios && partial_scenarios) ||
      !(setequal(current_cases, c("h1", "h3", "supplier")) ||
        setequal(current_cases, names(current_modes))) ||
      any(table(current$scenario, current$case, current$nprobe) != 1)) {
    stop("supplier summary must contain all six scenarios and comparison cases at every nprobe")
  }
  has_sweep <- length(unique(current$nprobe)) > 1
  if (partial_scenarios && !has_sweep) {
    stop("partial-scenario publication requires measured nprobe curves, not single points")
  }
  sweep_execution <- "per_point_process"
  if ("sweep_execution" %in% names(current)) {
    sweep_execution <- unique(current$sweep_execution)
    if (!is.character(sweep_execution) ||
        length(sweep_execution) != 1 || is.na(sweep_execution) ||
        !sweep_execution %in% c("per_point_process", "single_load_nprobe_array")) {
      stop("current points must declare one consistent, recognized sweep execution")
    }
  }
  supplier_degree_semantics <- "fresh_unvisited_neighbors"
  if ("supplier_degree_semantics" %in% names(current)) {
    supplier_degree_semantics <- unique(
      current$supplier_degree_semantics[current$case == "supplier"]
    )
    if (!is.character(supplier_degree_semantics) ||
        length(supplier_degree_semantics) != 1 || is.na(supplier_degree_semantics) ||
        !supplier_degree_semantics %in% c(
          "fresh_unvisited_neighbors", "predicate_valid_neighbors", "retained_eligible_ratio"
        )) {
      stop("supplier points must declare one consistent, recognized degree semantics")
    }
  }
  ratio_degree <- supplier_degree_semantics == "retained_eligible_ratio"
  predicate_degree <- supplier_degree_semantics == "predicate_valid_neighbors" || ratio_degree
  retained_ratio <- minimum_physical_degree <- NULL
  if (ratio_degree) {
    if (!all(c("retained_ratio", "minimum_physical_degree") %in% names(current))) {
      stop("ratio-degree points require retained_ratio and minimum_physical_degree")
    }
    supplier_rows <- current$case == "supplier"
    retained_ratio <- unique(current$retained_ratio[supplier_rows])
    minimum_physical_degree <- unique(current$minimum_physical_degree[supplier_rows])
    if (!is.numeric(retained_ratio) || length(retained_ratio) != 1 ||
        !is.finite(retained_ratio) || retained_ratio <= 0 || retained_ratio > 1 ||
        !is.numeric(minimum_physical_degree) || length(minimum_physical_degree) != 1 ||
        !is.finite(minimum_physical_degree) || minimum_physical_degree < 1 ||
        minimum_physical_degree != floor(minimum_physical_degree)) {
      stop("ratio-degree points require consistent valid ratio and physical-degree floor")
    }
  }
  if (any(!is.finite(current$recall_at_10)) ||
      any(current$recall_at_10 < 0 | current$recall_at_10 > 1) ||
      any(!is.finite(current$qps) | current$qps <= 0) ||
      any(!is.finite(current$ordinary_ms) | current$ordinary_ms <= 0) ||
      any(abs(current$qps - 1000 / current$ordinary_ms) > 1e-6)) {
    stop("invalid current recall/QPS or inconsistent ordinary-latency aggregation")
  }
  trials <- lapply(seq_len(nrow(current)), function(i) {
    values <- if (is.matrix(current$ordinary_ms_runs)) {
      current$ordinary_ms_runs[i, ]
    } else {
      unlist(current$ordinary_ms_runs[[i]])
    }
    if (length(values) < 2 || any(!is.finite(values) | values <= 0) ||
        abs(mean(values) - current$ordinary_ms[i]) > 1e-9) {
      stop("current points require at least two valid ordinary repetitions")
    }
    1000 / values
  })
  added <- data.frame(
    mode = unname(current_modes[current$case]),
    workload = ifelse(current$scenario == "extreme_tag", "sparse_tag", current$scenario),
    nprobe = current$nprobe, recall = current$recall_at_10, qps = current$qps,
    qps_trial_count = lengths(trials), qps_trial_min = vapply(trials, min, numeric(1)),
    qps_trial_max = vapply(trials, max, numeric(1)),
    graph_signature_pruning = NA, top_graph_maxcheck = NA
  )
  current_workloads <- unique(added$workload)
  rows <- rbind(rows, added[plot_columns])
}

navigation_labels <- c(
  H1Only = "H1-only (previous)",
  H2Only = "H2-only (previous)",
  H3Hierarchy = if (has_corrected_placement) {
    "H3 hierarchy (rebuilt)"
  } else if (has_routing_only) {
    "H3 hierarchy (routing-only)"
  } else if (has_fixed_hierarchy) {
    "H3 hierarchy (fixed)"
  } else {
    "H3 hierarchy"
  }
)
navigation_colors <- c(
  H1Only = "#2166AC", H2Only = "#B2182B", H3Hierarchy = "#1B9E77"
)
navigation_shapes <- c(H1Only = 16, H2Only = 17, H3Hierarchy = 15)
navigation_lines <- c(H1Only = "solid", H2Only = "solid", H3Hierarchy = "solid")
if (has_paired) {
  navigation_labels["H3Hierarchy"] <- "H3 (published, old budgets)"
  navigation_labels <- c(
    navigation_labels, H3PairedOld = "H3 old (paired rerun)",
    H3Current = "H3 current (lightweight rescue)"
  )
  navigation_colors <- c(navigation_colors, H3PairedOld = "#555555", H3Current = "#E66101")
  navigation_shapes <- c(navigation_shapes, H3PairedOld = 1, H3Current = 18)
  navigation_lines <- c(navigation_lines, H3PairedOld = "dashed", H3Current = "solid")
}
if (has_supplier) {
  navigation_labels["H3Current"] <- "H3 (Sep 10 lightweight rescue)"
  navigation_labels <- c(
    navigation_labels,
    H1Reference = "H1 reference (same run)",
    H3Reference = "H3 reference (same run)",
    GraphControl = "Graph control (predicate-first)",
    PostingSupplier = if (ratio_degree) {
      "H1 + supplier (retained-degree ratio)"
    } else if (predicate_degree) {
      "H1 + supplier (predicate-valid degree)"
    } else {
      "H1 + supplier (fresh-degree prototype)"
    }
  )
  navigation_colors <- c(
    navigation_colors, H1Reference = "#053061", H3Reference = "#00665E",
    GraphControl = "#333333", PostingSupplier = "#7B3294"
  )
  navigation_shapes <- c(
    navigation_shapes, H1Reference = 4, H3Reference = 3,
    GraphControl = 5, PostingSupplier = 8
  )
  navigation_lines <- c(
    navigation_lines, H1Reference = if (has_sweep) "dotdash" else "blank",
    H3Reference = if (has_sweep) "dotdash" else "blank",
    GraphControl = if (has_sweep) "dotted" else "blank",
    PostingSupplier = if (has_sweep) "solid" else "blank"
  )
}
if (any(!rows$mode %in% names(navigation_labels))) {
  stop("unknown navigation mode in benchmark results")
}
rows$mode <- factor(rows$mode, levels = names(navigation_labels))
has_hierarchy <- any(rows$mode == "H3Hierarchy")
active_modes <- names(navigation_labels)[names(navigation_labels) %in% rows$mode]
probe_limits <- range(if (has_sweep) rows$nprobe else rows$nprobe[!rows$mode %in% current_modes])
probe_breaks <- if (has_sweep) c(16, 32, 128, 384, 1024) else c(32, 128, 384, 1024)
probe_breaks <- probe_breaks[
  probe_breaks >= probe_limits[1] & probe_breaks <= probe_limits[2]
]
workload_labels <- c(
  unfilter = "unfilter (selectivity: 100%)",
  broad_tag = "broad tag (selectivity: 17.0092%)",
  medium_tag = "medium tag (selectivity: 1.7009%)",
  sparse_tag = "isolated extreme tag (selectivity: 0.0193%)",
  mixed_dnf = "mixed DNF (selectivity: 0.0608%)"
)
if (has_supplier) {
  workload_labels <- c(
    workload_labels, numeric = if (partial_scenarios && !"numeric" %in% current_workloads) {
      "numeric (corrected sweep pending)"
    } else if (has_sweep) {
      "numeric (current sweep only)"
    } else {
      "numeric (current measurements only)"
    }
  )
}
if (any(!rows$workload %in% names(workload_labels))) {
  stop("unknown workload in benchmark results")
}
if (has_supplier) {
  write.csv(rows, paste0(output, ".plot-data.csv"), row.names = FALSE)
  source_identity <- function(path) {
    list(path = normalizePath(path, mustWork = TRUE),
         md5 = unname(tools::md5sum(path)))
  }
  write_json(
    list(
      historical = source_identity(input), paired = source_identity(args[[3]]),
      current = source_identity(args[[4]]),
      current_case_modes = as.list(current_modes),
      current_scenarios = as.list(current_scenarios),
      missing_current_scenarios = as.list(missing_current_scenarios),
      partial_scenarios = partial_scenarios,
      measurement_scope_note = if (partial_scenarios) {
        "Frozen measured stage only; later single-point diagnostics are not spliced into curves."
      } else NULL,
      current_nprobe = sort(unique(current$nprobe)),
      current_qps = "1000 / mean ordinary latency_ms; per-run min/max bars",
      current_scope = "1000 warmup + 1000 measured, O_DIRECT, search pages15",
      sweep_execution = sweep_execution,
      supplier_degree_semantics = supplier_degree_semantics,
      retained_ratio = retained_ratio,
      minimum_physical_degree = minimum_physical_degree,
      supplier_trigger_warning = if (predicate_degree) NULL else paste(
        "Frozen prototype counts only unvisited eligible neighbors.",
        "This is a known defect: visited neighbors should count toward predicate-valid degree."
      ),
      comparison_warning = "Historical IO, budgets, query windows and aggregation differ.",
      current_points = nrow(current), total_points = nrow(rows),
      current_is_measured_sweep = has_sweep
    ),
    paste0(output, ".plot-provenance.json"), auto_unbox = TRUE, pretty = TRUE,
    null = "null"
  )
}
rows$workload <- factor(
  rows$workload,
  levels = names(workload_labels),
  labels = unname(workload_labels)
)
rows <- rows[order(rows$workload, rows$mode, rows$nprobe), ]

make_workload_plot <- function(workload_id, full_recall_range) {
  workload_rows <- rows[
    rows$workload == unname(workload_labels[workload_id]),
  ]
  if (nrow(workload_rows) == 0) {
    return(
      ggplot() +
        annotate("text", x = 0.5, y = 0.5, size = 4, color = "grey35",
                 label = "No corrected nprobe sweep available\nObsolete prototype data removed") +
        xlim(0, 1) + ylim(0, 1) +
        labs(title = unname(workload_labels[workload_id])) +
        theme_void(base_size = 10) +
        theme(plot.title = element_text(size = 10))
    )
  }
  if (has_supplier && any(workload_rows$recall < 0.6)) full_recall_range <- TRUE
  historical <- workload_rows[!workload_rows$mode %in% current_modes, ]
  latest <- workload_rows[workload_rows$mode %in% current_modes, ]
  x_breaks <- if (full_recall_range) {
    c(0, 0.2, 0.4, 0.6, 0.8, 1.0)
  } else {
    c(0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
  }
  x_limits <- if (full_recall_range) c(0, 1) else c(0.6, 1)

  ggplot(workload_rows, aes(
    x = recall, y = qps, color = mode, shape = mode, group = mode
  )) +
    {if (nrow(historical) > 0) geom_path(
      data = historical, aes(linetype = mode), linewidth = 0.65
    )} +
    {if (has_sweep) geom_path(
      data = latest, aes(linetype = mode), linewidth = 0.9
    )} +
    {if (has_paired) geom_linerange(
      data = workload_rows[workload_rows$mode %in% c("H3PairedOld", "H3Current"), ],
      aes(ymin = qps_trial_min, ymax = qps_trial_max), linewidth = 0.45
    )} +
    geom_point(data = historical, aes(size = nprobe), alpha = 0.9) +
    {if (has_supplier) geom_linerange(
      data = latest, aes(ymin = qps_trial_min, ymax = qps_trial_max),
      linewidth = 0.6
    )} +
    {if (has_sweep) {
      geom_point(data = latest, aes(size = nprobe), stroke = 1)
    } else if (has_supplier) {
      geom_point(data = latest, size = 3.6, stroke = 1.1)
    }} +
    {if (has_supplier && !has_sweep) geom_text(
      data = latest[latest$mode == "PostingSupplier", ],
      aes(label = paste0(round(qps), " QPS")),
      hjust = 1.08, vjust = -0.7, size = 2.7, fontface = "bold"
    )} +
    geom_vline(
      xintercept = c(0.90, 0.95), linetype = "dashed",
      color = "grey35", linewidth = 0.45
    ) +
    scale_color_manual(values = navigation_colors, labels = navigation_labels) +
    scale_shape_manual(values = navigation_shapes, labels = navigation_labels) +
    {if (nrow(historical) > 0 || has_sweep) scale_linetype_manual(values = navigation_lines)} +
    scale_size_continuous(
      range = c(1.8, 4.8), limits = probe_limits, breaks = probe_breaks
    ) +
    scale_x_continuous(breaks = x_breaks) +
    coord_cartesian(xlim = x_limits, ylim = if (has_supplier) c(0, NA) else NULL) +
    labs(
      x = "Recall@10", y = "Queries per second",
      color = "Navigation", shape = "Navigation", size = "nprobe",
      title = unname(workload_labels[workload_id]),
      subtitle = if (partial_scenarios && nrow(latest) == 0) {
        "Historical baselines only; corrected sweep pending"
      } else NULL
    ) +
    theme_bw(base_size = if (has_supplier) 10 else 9) +
    theme(
      legend.position = "none",
      panel.grid.minor = element_blank(),
      plot.title = element_text(size = if (has_supplier) 10 else 9)
    )
}

workload_order <- names(workload_labels)
plots <- lapply(
  workload_order,
  function(workload_id) {
    make_workload_plot(workload_id, workload_id == "sparse_tag")
  }
)

draw_comparison_legend <- function() {
  grid::grid.text(
    "Navigation", x = 0.12, y = 0.91, just = "left",
    gp = grid::gpar(fontface = "bold", fontsize = 10)
  )
  for (index in seq_along(active_modes)) {
    mode <- active_modes[index]
    y <- 0.83 - (index - 1) * 0.07
    grid::grid.lines(
      x = c(0.12, 0.25), y = c(y, y),
      gp = grid::gpar(
        col = navigation_colors[[mode]], lwd = 1.5, lty = navigation_lines[[mode]]
      )
    )
    grid::grid.points(
      x = 0.185, y = y, pch = navigation_shapes[[mode]],
      size = grid::unit(3, "mm"),
      gp = grid::gpar(col = navigation_colors[[mode]])
    )
    grid::grid.text(
      navigation_labels[[mode]], x = 0.29, y = y, just = "left",
      gp = grid::gpar(fontsize = if (has_paired) 8 else 9)
    )
  }
  size_shift <- if (has_paired) -0.04 else 0
  grid::grid.text(
    "Point size: nprobe", x = 0.12, y = 0.54 + size_shift, just = "left",
    gp = grid::gpar(fontsize = 9)
  )
  size_scale <- ggplot_build(plots[[1]])$plot$scales$get_scales("size")
  point_sizes <- size_scale$map(probe_breaks)
  positions <- seq(0.18, 0.8, length.out = length(probe_breaks))
  for (index in seq_along(probe_breaks)) {
    grid::grid.points(
      x = positions[index], y = 0.46 + size_shift, pch = 16,
      size = grid::unit(point_sizes[index], "mm"),
      gp = grid::gpar(col = "grey40")
    )
    grid::grid.text(
      probe_breaks[index], x = positions[index], y = 0.4 + size_shift,
      gp = grid::gpar(fontsize = 8)
    )
  }
  notes <- c(
    "Vertical guides: 90% / 95% recall.",
    "100 warm-up + 900 measured queries; 1 thread."
  )
  if (has_hierarchy) {
    notes <- c(
      notes,
      "H3: top graph; H2/H1 local-ID CSR.",
      "Adjacent-layer ratio = 0.15; replicas = 8."
    )
  }
  if (has_routing_only) {
    notes <- c(
      notes,
      "Separate vectors/state; only H1/SSD yield results."
    )
  } else if (has_fixed_hierarchy) {
    notes <- c(
      notes,
      "H3: fixed routing; consult run metadata for historical completion policy."
    )
  }
  hierarchy_rows <- rows[rows$mode == "H3Hierarchy", , drop = FALSE]
  if ("graph_signature_pruning" %in% names(hierarchy_rows)) {
    graph_modes <- unique(na.omit(hierarchy_rows$graph_signature_pruning))
    if (length(graph_modes) > 1) {
      stop("select one graph-pruning mode before publishing the H3 curve")
    }
    if (length(graph_modes) == 1) {
      notes <- c(notes, paste0(
        "H2/H3 head signatures on; graph pruning ",
        if (graph_modes[[1]]) "on." else "off."
      ))
    }
  }
  median_three <- "qps_trial_count" %in% names(hierarchy_rows) &&
    any(hierarchy_rows$qps_trial_count == 3, na.rm = TRUE)
  bounded_graph <- nrow(hierarchy_rows) > 0 &&
      "top_graph_maxcheck" %in% names(hierarchy_rows) &&
      all(!is.na(hierarchy_rows$top_graph_maxcheck)) &&
      all(hierarchy_rows$top_graph_maxcheck == pmax(128, 2 * hierarchy_rows$nprobe))
  if (median_three && bounded_graph) {
    notes <- c(notes, "H3: 3-run median; MaxCheck = max(128, 2*nprobe).")
  } else if (median_three) {
    notes <- c(notes, "H3 QPS: median of 3 serial runs.")
  } else if (bounded_graph) {
    notes <- c(notes, "H3 top MaxCheck = max(128, 2*nprobe).")
  }
  notes <- c(notes, "Historical H1/H2 measurements kept unchanged.")
  if (has_paired) {
    notes <- c(
      "100 warm-up + 900 measured queries; 1 thread.",
      "Paired rerun: probes 32/64/128/256; 3-run medians.",
      "Vertical bars: paired QPS min/max (not confidence intervals).",
      "Paired: MaxCheck 2048; hierarchy 512; search pages 12.",
      "Published H3: MaxCheck 8192; hierarchy max(128, 2*nprobe).",
      "Historical curves use their original, different budgets.",
      "Current: shared ratio 0.16; H build cut 16 + rescue tail.",
      "End-to-end comparison, not an isolated rescue effect."
    )
  }
  grid::grid.text(
    paste(notes, collapse = "\n"),
    x = 0.12, y = 0.36 + size_shift, just = c("left", "top"),
    gp = grid::gpar(fontsize = if (has_paired) 6.8 else 7.5, lineheight = 1.15)
  )
}

draw_supplier_legend <- function() {
  groups <- list(
    list(x = 0.025, title = "Historical curves", modes = setdiff(active_modes, current_modes)),
    list(
      x = 0.36,
      title = if (partial_scenarios) {
        paste0("Corrected frozen sweep (", length(current_scenarios), "/6 scenarios)")
      } else if (has_sweep) {
        "Current measured nprobe sweep"
      } else {
        paste0("Current measurements (nprobe", unique(current$nprobe), ")")
      },
      modes = intersect(unname(current_modes), active_modes)
    )
  )
  for (group in groups) {
    grid::grid.text(
      group$title, x = group$x, y = 0.91, just = "left",
      gp = grid::gpar(fontface = "bold", fontsize = 10)
    )
    for (index in seq_along(group$modes)) {
      mode <- group$modes[index]
      y <- 0.79 - (index - 1) * 0.135
      if (!mode %in% current_modes || has_sweep) grid::grid.lines(
        x = group$x + c(0, 0.045), y = c(y, y),
        gp = grid::gpar(col = navigation_colors[[mode]], lwd = 1.5,
                       lty = navigation_lines[[mode]])
      )
      grid::grid.points(
        x = group$x + 0.0225, y = y, pch = navigation_shapes[[mode]],
        size = grid::unit(3.5, "mm"),
        gp = grid::gpar(col = navigation_colors[[mode]])
      )
      grid::grid.text(
        navigation_labels[[mode]], x = group$x + 0.06, y = y, just = "left",
        gp = grid::gpar(fontsize = 8.2)
      )
    }
  }
  grid::grid.text(
    if (has_sweep) "Point size: nprobe (all curves)" else "Historical point size: nprobe",
    x = 0.36, y = 0.23, just = "left",
    gp = grid::gpar(fontsize = 8.5)
  )
  scale <- ggplot_build(plots[[1]])$plot$scales$get_scales("size")
  positions <- seq(0.40, 0.64, length.out = length(probe_breaks))
  for (index in seq_along(probe_breaks)) {
    grid::grid.points(
      x = positions[index], y = 0.14, pch = 16,
      size = grid::unit(scale$map(probe_breaks)[index], "mm"),
      gp = grid::gpar(col = "grey40")
    )
    grid::grid.text(probe_breaks[index], x = positions[index], y = 0.055,
                    gp = grid::gpar(fontsize = 8))
  }
  notes <- c(
    if (has_sweep) {
      paste0("Current: O_DIRECT, nprobe ", min(current$nprobe), "-", max(current$nprobe), ", pages15.")
    } else {
      "New points: O_DIRECT, nprobe24, search pages15."
    },
    "1000 warm-up + 1000 measured queries; 1 thread.",
    "QPS = 1000 / mean ordinary latency; repeated runs.",
    "Current bars: run min/max, not confidence intervals.",
    if (sweep_execution == "single_load_nprobe_array") {
      "One index load per native nprobe-array process."
    } else {
      "Each probe/repetition uses a separate process."
    },
    "",
    "Historical curves retain their original IO and budgets.",
    "Paired H3: 100 + 900 queries, three-run medians;",
    "probes32/64/128/256, search pages12.",
    if (has_sweep) {
      "Compare same-run curves for controlled differences."
    } else {
      "Compare same-run markers for controlled differences."
    },
    if (has_sweep) {
      paste0("Each current curve has ", length(unique(current$nprobe)), " measured nprobe points.")
    } else {
      "Supplier: one measured point per task, not a curve."
    },
    if (partial_scenarios) {
      c(
        "Unmeasured scenarios have no replacement curves.",
        "Frozen native-reuse sweep; later single-point fixes",
        "are not spliced into this measurement set."
      )
    } else {
      "Numeric panel contains current measurements only."
    },
    if (ratio_degree) {
      c(
        paste0(
          "Supplier: d >= ", minimum_physical_degree,
          " and eligible/d < ", format(retained_ratio, trim = TRUE), "."
        ),
        "Visited does not reduce eligible degree."
      )
    } else if (predicate_degree) {
      c(
        "Supplier degree includes visited eligible neighbors.",
        "Visited suppresses repeated work, not connectivity."
      )
    } else {
      c(
        "Supplier counts only unvisited eligible neighbors:",
        "known trigger defect, not predicate-valid degree."
      )
    }
  )
  grid::grid.text(
    paste(notes, collapse = "\n"), x = 0.70, y = 0.91, just = c("left", "top"),
    gp = grid::gpar(fontsize = 7.7, lineheight = 1.25)
  )
}

draw_combined_plot <- function() {
  grid::grid.newpage()
  layout <- grid::grid.layout(
    if (has_supplier) 4 else 3, 3,
    heights = if (has_supplier) grid::unit(
      c(0.045, 0.3525, 0.3525, 0.25), "npc"
    ) else grid::unit.c(
      grid::unit(0.05, "npc"),
      grid::unit(0.475, "npc"),
      grid::unit(0.475, "npc")
    )
  )
  grid::pushViewport(grid::viewport(layout = layout))
  grid::pushViewport(grid::viewport(
    layout.pos.row = 1,
    layout.pos.col = 1:3
  ))
  grid::grid.text(
    if (partial_scenarios) {
      "SIFT1M recall-QPS: historical baselines + corrected native-reuse stage"
    } else if (has_sweep) {
      "SIFT1M recall-QPS: historical curves + current nprobe sweep"
    } else if (has_supplier) {
      "SIFT1M recall-QPS: historical curves + H1 posting-neighbor supplier"
    } else if (has_paired) {
      "SIFT1M recall-QPS: historical curves and current H3 paired comparison"
    } else if (has_hierarchy) {
      "SIFT1M H1-only vs H2-only vs H3 hierarchy"
    } else {
      "SIFT1M H1-only vs H2-only head navigation"
    },
    gp = grid::gpar(fontface = "bold", fontsize = if (has_supplier) 15 else 12)
  )
  grid::popViewport()
  for (index in seq_along(plots)) {
    grid::pushViewport(grid::viewport(
      layout.pos.row = (index - 1) %/% 3 + 2,
      layout.pos.col = (index - 1) %% 3 + 1
    ))
    print(plots[[index]], newpage = FALSE)
    grid::popViewport()
  }
  grid::pushViewport(grid::viewport(
    layout.pos.row = if (has_supplier) 4 else 3,
    layout.pos.col = if (has_supplier) 1:3 else 3
  ))
  if (has_supplier) draw_supplier_legend() else draw_comparison_legend()
  grid::popViewport()
  grid::popViewport()
}

width <- if (has_supplier) 14 else 11
height <- if (has_supplier) 10.5 else 7
grDevices::pdf(paste0(output, ".pdf"), width = width, height = height)
draw_combined_plot()
grDevices::dev.off()
grDevices::png(paste0(output, ".png"), width = width * 180, height = height * 180, res = 180)
draw_combined_plot()
grDevices::dev.off()

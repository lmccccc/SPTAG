#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (!length(args) %in% c(2, 3)) {
  stop(paste(
    "usage: plot_sift1m_h1_h2_curve.R <results.jsonl> <output-prefix>",
    "[paired-results.summary.csv]"
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

has_paired <- length(args) == 3
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
if (any(!rows$mode %in% names(navigation_labels))) {
  stop("unknown navigation mode in benchmark results")
}
rows$mode <- factor(rows$mode, levels = names(navigation_labels))
has_hierarchy <- any(rows$mode == "H3Hierarchy")
active_modes <- names(navigation_labels)[names(navigation_labels) %in% rows$mode]
probe_limits <- range(rows$nprobe)
probe_breaks <- c(32, 128, 384, 1024)
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
  x_breaks <- if (full_recall_range) {
    c(0, 0.2, 0.4, 0.6, 0.8, 1.0)
  } else {
    c(0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
  }
  x_limits <- if (full_recall_range) c(0, 1) else c(0.6, 1)

  ggplot(workload_rows, aes(
    x = recall, y = qps, color = mode, shape = mode, group = mode
  )) +
    geom_path(aes(linetype = mode), linewidth = 0.65) +
    {if (has_paired) geom_linerange(
      data = workload_rows[workload_rows$mode %in% c("H3PairedOld", "H3Current"), ],
      aes(ymin = qps_trial_min, ymax = qps_trial_max), linewidth = 0.45
    )} +
    geom_point(aes(size = nprobe), alpha = 0.9) +
    geom_vline(
      xintercept = c(0.90, 0.95), linetype = "dashed",
      color = "grey35", linewidth = 0.45
    ) +
    scale_color_manual(values = navigation_colors, labels = navigation_labels) +
    scale_shape_manual(values = navigation_shapes, labels = navigation_labels) +
    scale_linetype_manual(values = navigation_lines) +
    scale_size_continuous(
      range = c(1.8, 4.8), limits = probe_limits, breaks = probe_breaks
    ) +
    scale_x_continuous(breaks = x_breaks) +
    coord_cartesian(xlim = x_limits) +
    labs(
      x = "Recall@10", y = "Queries per second",
      color = "Navigation", shape = "Navigation", size = "nprobe",
      title = unname(workload_labels[workload_id])
    ) +
    theme_bw(base_size = 9) +
    theme(
      legend.position = "none",
      panel.grid.minor = element_blank(),
      plot.title = element_text(size = 9)
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

draw_combined_plot <- function() {
  grid::grid.newpage()
  layout <- grid::grid.layout(
    3, 3,
    heights = grid::unit.c(
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
    if (has_paired) {
      "SIFT1M recall-QPS: historical curves and current H3 paired comparison"
    } else if (has_hierarchy) {
      "SIFT1M H1-only vs H2-only vs H3 hierarchy"
    } else {
      "SIFT1M H1-only vs H2-only head navigation"
    },
    gp = grid::gpar(fontface = "bold", fontsize = 12)
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
    layout.pos.row = 3,
    layout.pos.col = 3
  ))
  draw_comparison_legend()
  grid::popViewport()
  grid::popViewport()
}

grDevices::pdf(paste0(output, ".pdf"), width = 11, height = 7)
draw_combined_plot()
grDevices::dev.off()
grDevices::png(paste0(output, ".png"), width = 1980, height = 1260, res = 180)
draw_combined_plot()
grDevices::dev.off()

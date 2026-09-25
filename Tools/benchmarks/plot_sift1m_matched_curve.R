#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
usage <- paste(
  "usage: plot_sift1m_matched_curve.R <summary.json> <protocol.json>",
  "<output-prefix> [--partial-scenarios] [--points <points.json>]"
)
if (length(args) < 3) stop(usage)
allow_partial <- FALSE
points_path <- NULL
position <- 4L
while (position <= length(args)) {
  option <- args[[position]]
  if (option == "--partial-scenarios" && !allow_partial) {
    allow_partial <- TRUE
  } else if (option == "--points" && is.null(points_path) && position < length(args)) {
    position <- position + 1L
    points_path <- args[[position]]
  } else {
    stop(usage)
  }
  position <- position + 1L
}
if (!is.null(points_path) && startsWith(points_path, "--")) {
  stop(usage)
}
suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
})

rows <- fromJSON(args[[1]])
protocol <- fromJSON(args[[2]])
output <- args[[3]]
scenarios <- c("unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf")
cases <- c("h1_original", "h1", "h3", "supplier")
fingerprints <- c(
  "protocol_id", "index_fingerprint", "harness_fingerprint",
  "original_core_fingerprint", "current_core_fingerprint"
)
required_protocol <- c(
  fingerprints, "io_mode", "topk", "maxcheck", "hierarchy_maxcheck",
  "hierarchy_initial_probe_ratio", "page_limit", "query_count", "measure_offset",
  "warmup", "query_threads", "numa_cpu_node", "numa_memory_node", "nprobe",
  "repetitions", "retained_ratio", "minimum_physical_degree",
  "original_reference_verified", "timed_body_shared"
)
if (!is.list(protocol) || !all(required_protocol %in% names(protocol))) {
  stop("missing matched protocol fields")
}
sha256 <- function(value) {
  is.character(value) && length(value) == 1 && !is.na(value) &&
    grepl("^[0-9a-f]{64}$", value)
}
if (!all(vapply(protocol[fingerprints], sha256, logical(1))) ||
    protocol$original_core_fingerprint == protocol$current_core_fingerprint ||
    !isTRUE(protocol$original_reference_verified) || !isTRUE(protocol$timed_body_shared)) {
  stop("matched comparison requires distinct authenticated cores and a shared timed body")
}
integer_value <- function(value, minimum) {
  is.numeric(value) && length(value) == 1 && is.finite(value) &&
    value >= minimum && value == floor(value)
}
positive <- c(
  "topk", "maxcheck", "hierarchy_maxcheck", "page_limit", "query_count",
  "query_threads", "minimum_physical_degree"
)
nonnegative <- c("measure_offset", "warmup", "numa_cpu_node", "numa_memory_node")
if (!all(vapply(protocol[positive], integer_value, logical(1), minimum = 1)) ||
    !all(vapply(protocol[nonnegative], integer_value, logical(1), minimum = 0)) ||
    !integer_value(protocol$repetitions, 2) ||
    !identical(protocol$io_mode, "buffered") || protocol$topk != 10) {
  stop("invalid matched IO, budget, query-window or repetition settings")
}
for (field in c("retained_ratio", "hierarchy_initial_probe_ratio")) {
  value <- protocol[[field]]
  if (!is.numeric(value) || length(value) != 1 || !is.finite(value) ||
      value <= 0 || value > 1) stop("invalid matched ratio")
}
grid <- protocol$nprobe
if (!is.numeric(grid) || length(grid) < 2 || any(!is.finite(grid)) ||
    any(grid < protocol$topk | grid != floor(grid)) || anyDuplicated(grid)) {
  stop("matched publication requires a declared complete nprobe array, not single points")
}
required_rows <- c(
  "scenario", "case", "nprobe", "recall_at_10", "qps", "ordinary_ms",
  "ordinary_ms_runs", "protocol_id", "index_fingerprint",
  "harness_fingerprint", "core_fingerprint", "sweep_execution"
)
if (!is.data.frame(rows) || nrow(rows) == 0 ||
    !all(required_rows %in% names(rows)) || anyNA(rows[setdiff(required_rows, "ordinary_ms_runs")])) {
  stop("missing matched measurement fields")
}
for (field in c("protocol_id", "index_fingerprint", "harness_fingerprint")) {
  if (!is.character(rows[[field]]) || any(rows[[field]] != protocol[[field]])) {
    stop(paste("mixed or mismatched", field))
  }
}
expected_cores <- ifelse(
  rows$case == "h1_original",
  protocol$original_core_fingerprint, protocol$current_core_fingerprint
)
if (!is.character(rows$core_fingerprint) || any(rows$core_fingerprint != expected_cores) ||
    any(rows$sweep_execution != "single_load_nprobe_array")) {
  stop("wrong per-case core identity or non-native array execution")
}
present <- scenarios[scenarios %in% rows$scenario]
missing <- setdiff(scenarios, present)
if (length(missing) && !allow_partial) {
  stop("all six scenarios are required unless --partial-scenarios is explicit")
}
if (!is.numeric(rows$nprobe) || any(!rows$scenario %in% scenarios) ||
    !setequal(rows$case, cases) || !setequal(rows$nprobe, grid) ||
    anyDuplicated(rows[c("scenario", "case", "nprobe")]) ||
    any(table(
      factor(rows$scenario, levels = present),
      factor(rows$case, levels = cases),
      factor(rows$nprobe, levels = grid)
    ) != 1)) {
  stop("every included scenario requires all four cases at every declared nprobe")
}
if (!all(vapply(rows[c("recall_at_10", "qps", "ordinary_ms")], is.numeric, logical(1))) ||
    any(!is.finite(rows$recall_at_10) | rows$recall_at_10 < 0 | rows$recall_at_10 > 1) ||
    any(!is.finite(rows$qps) | rows$qps <= 0) ||
    any(!is.finite(rows$ordinary_ms) | rows$ordinary_ms <= 0) ||
    any(abs(rows$qps - 1000 / rows$ordinary_ms) > 1e-6)) {
  stop("invalid measured recall or latency-derived QPS")
}
trials <- lapply(seq_len(nrow(rows)), function(i) {
  values <- if (is.matrix(rows$ordinary_ms_runs)) {
    rows$ordinary_ms_runs[i, ]
  } else {
    unlist(rows$ordinary_ms_runs[[i]])
  }
  if (!is.numeric(values) || length(values) != protocol$repetitions ||
      any(!is.finite(values) | values <= 0) ||
      abs(mean(values) - rows$ordinary_ms[i]) > 1e-9) {
    stop("measurement repetitions disagree with protocol or mean latency")
  }
  values
})
rows$qps_trial_min <- vapply(trials, function(value) min(1000 / value), numeric(1))
rows$qps_trial_max <- vapply(trials, function(value) max(1000 / value), numeric(1))
rows$ordinary_ms_runs <- vapply(
  trials, function(value) paste(format(value, digits = 17, trim = TRUE), collapse = ";"),
  character(1)
)
rows <- rows[order(match(rows$scenario, scenarios), match(rows$case, cases), rows$nprobe), ]

points <- NULL
point_input <- NULL
point_scenarios <- character()
point_cases <- c("auto", "graph", "estimate_only", "original", "h3")
if (!is.null(points_path)) {
  point_input <- fromJSON(points_path)
  common_fields <- c(
    "index_fingerprint", "io_mode", "topk", "maxcheck", "hierarchy_maxcheck",
    "hierarchy_initial_probe_ratio", "page_limit", "query_count", "measure_offset",
    "warmup", "query_threads", "numa_cpu_node", "numa_memory_node", "repetitions"
  )
  if (!is.list(point_input) || !is.character(point_input$label) ||
      length(point_input$label) != 1 || is.na(point_input$label) ||
      !nzchar(point_input$label) || !is.list(point_input$provenance) ||
      !all(common_fields %in% names(point_input$common))) {
    stop("single-point overlay requires label, provenance and common measurement settings")
  }
  for (field in common_fields) {
    if (!isTRUE(all.equal(point_input$common[[field]], protocol[[field]],
                         tolerance = 1e-12, check.attributes = FALSE))) {
      stop(paste("single-point overlay mismatches", field))
    }
  }
  body <- point_input$common$timed_body_fingerprint
  if (!sha256(body) || !"common_timed_body_fingerprint" %in% names(rows) ||
      anyNA(rows$common_timed_body_fingerprint) ||
      any(rows$common_timed_body_fingerprint != body)) {
    stop("single-point overlay requires matching ordinary timed-body fingerprints")
  }
  points <- point_input$rows
  point_fields <- c(
    "scenario", "case", "nprobe", "recall_at_10", "qps", "ordinary_ms", "ordinary_ms_runs"
  )
  if (!is.data.frame(points) || !nrow(points) ||
      !all(point_fields %in% names(points)) ||
      anyNA(points[setdiff(point_fields, "ordinary_ms_runs")]) ||
      any(!points$scenario %in% scenarios) || any(!points$case %in% point_cases) ||
      anyDuplicated(points[c("scenario", "case", "nprobe")])) {
    stop("invalid or duplicated single-point measurements")
  }
  for (scenario in unique(points$scenario)) {
    if (!all(c("auto", "graph") %in% points$case[points$scenario == scenario])) {
      stop("each single-point scenario requires auto and graph-only controls")
    }
  }
  if (!all(vapply(points[c("nprobe", "recall_at_10", "qps", "ordinary_ms")],
                  is.numeric, logical(1))) ||
      any(!is.finite(points$nprobe) | points$nprobe < protocol$topk |
          points$nprobe != floor(points$nprobe)) ||
      any(!is.finite(points$recall_at_10) | points$recall_at_10 < 0 | points$recall_at_10 > 1) ||
      any(!is.finite(points$qps) | points$qps <= 0) ||
      any(!is.finite(points$ordinary_ms) | points$ordinary_ms <= 0) ||
      any(abs(points$qps - 1000 / points$ordinary_ms) > 1e-6)) {
    stop("invalid single-point recall or latency-derived QPS")
  }
  point_trials <- lapply(seq_len(nrow(points)), function(i) {
    value <- if (is.matrix(points$ordinary_ms_runs)) {
      points$ordinary_ms_runs[i, ]
    } else unlist(points$ordinary_ms_runs[[i]])
    if (!is.numeric(value) || length(value) != protocol$repetitions ||
        any(!is.finite(value) | value <= 0) ||
        abs(mean(value) - points$ordinary_ms[i]) > 1e-9) {
      stop("single-point repetitions disagree with mean latency or protocol")
    }
    value
  })
  points$qps_trial_min <- vapply(point_trials, function(x) min(1000 / x), numeric(1))
  points$qps_trial_max <- vapply(point_trials, function(x) max(1000 / x), numeric(1))
  points$ordinary_ms_runs <- vapply(
    point_trials, function(x) paste(format(x, digits = 17, trim = TRUE), collapse = ";"),
    character(1)
  )
  points$source_case <- points$case
  points$case <- paste0("cost_", points$case)
  point_scenarios <- unique(points$scenario)
}
has_points <- !is.null(points)

scenario_labels <- c(
  unfilter = "Unfiltered", broad_tag = "Broad categorical",
  medium_tag = "Medium categorical", extreme_tag = "Extreme categorical",
  numeric = "Numeric range", mixed_dnf = "Mixed DNF"
)
if (has_points) {
  old_only <- setdiff(present, point_scenarios)
  scenario_labels[old_only] <- paste0(
    scenario_labels[old_only], "\nOld curves only; new policy not measured"
  )
}
case_labels <- c(
  h1_original = "H1: pre-supplier core", h1 = "H1: current core, supplier off",
  h3 = "H3: native hierarchy", supplier = "H1 + posting supplier"
)
colors <- c(h1_original = "#2166AC", h1 = "#666666", h3 = "#1B9E77", supplier = "#D95F02")
shapes <- setNames(c(16, 1, 15, 17), cases)
line_types <- setNames(c("solid", "longdash", "dotted", "dotdash"), cases)
if (has_points) {
  case_labels <- c(
    h1_original = "H1 pre-supplier curve", h1 = "Old core: supplier off",
    h3 = "H3 curve", supplier = "Old degree-deficit supplier",
    cost_auto = paste0(point_input$label, ": auto (points)"),
    cost_graph = paste0(point_input$label, ": graph-only (points)"),
    cost_estimate_only = paste0(point_input$label, ": estimate-only (points)"),
    cost_original = "Fresh H1 reference (points)", cost_h3 = "Fresh H3 reference (points)"
  )
  colors <- c(colors, cost_auto = "#762A83", cost_graph = "#C51B7D",
              cost_estimate_only = "#8C510A", cost_original = "#2166AC", cost_h3 = "#1B9E77")
  shapes <- c(shapes, cost_auto = 8, cost_graph = 23, cost_estimate_only = 0,
              cost_original = 4, cost_h3 = 3)
  line_types <- c(line_types, setNames(rep("solid", 5), paste0("cost_", point_cases)))
}
all_cases <- names(case_labels)
plot_rows <- rows
plot_rows$scenario <- factor(plot_rows$scenario, levels = scenarios)
plot_rows$case <- factor(plot_rows$case, levels = all_cases)
empty_scenarios <- setdiff(missing, point_scenarios)
pending <- data.frame(
  scenario = factor(empty_scenarios, levels = scenarios),
  x = rep(0.925, length(empty_scenarios)), y = rep(1, length(empty_scenarios)),
  label = rep("No measurements available\nExperiments remain stopped", length(empty_scenarios))
)
if (has_points) {
  point_plot <- points
  point_plot$scenario <- factor(point_plot$scenario, levels = scenarios)
  point_plot$case <- factor(point_plot$case, levels = all_cases)
  auto_labels <- point_plot[point_plot$source_case == "auto", ]
  auto_labels$label <- sprintf("auto n%d\n%.0f QPS", auto_labels$nprobe, auto_labels$qps)
  auto_labels$label_x <- auto_labels$recall_at_10 - 0.02
  point_probe_note <- paste(vapply(
    unique(points$source_case), function(case) {
      paste0(case, " n", paste(sort(unique(points$nprobe[points$source_case == case])), collapse = "/"))
    }, character(1)
  ), collapse = "; ")
}
chart <- ggplot(plot_rows, aes(
  x = recall_at_10, y = qps, color = case, shape = case, linetype = case, group = case
)) +
  geom_vline(xintercept = c(0.90, 0.95), color = "grey75", linetype = "dotted") +
  geom_linerange(aes(ymin = qps_trial_min, ymax = qps_trial_max), linewidth = 0.4) +
  geom_path(linewidth = 0.7, alpha = if (has_points) 0.55 else 1) +
  geom_point(aes(size = nprobe), stroke = 0.8, alpha = if (has_points) 0.55 else 1) +
  {if (has_points) geom_linerange(
    data = point_plot, aes(ymin = qps_trial_min, ymax = qps_trial_max), linewidth = 0.8
  )} +
  {if (has_points) geom_point(data = point_plot, size = 4, stroke = 1.3)} +
  {if (has_points) geom_segment(
    data = auto_labels,
    aes(x = label_x + 0.002, xend = recall_at_10 - 0.002, y = qps, yend = qps),
    inherit.aes = FALSE, color = colors[["cost_auto"]], linewidth = 0.4
  )} +
  {if (has_points) geom_text(
    data = auto_labels, aes(x = label_x, y = qps, label = label),
    inherit.aes = FALSE, color = colors[["cost_auto"]], size = 3,
    hjust = 1, vjust = 0.5, fontface = "bold"
  )} +
  geom_text(data = pending, aes(x = x, y = y, label = label),
            inherit.aes = FALSE, color = "grey40", size = 3.4) +
  facet_wrap(~scenario, ncol = 3, scales = "free", drop = FALSE,
             labeller = as_labeller(scenario_labels)) +
  scale_color_manual(values = colors, breaks = all_cases, labels = case_labels) +
  scale_shape_manual(values = shapes, breaks = all_cases, labels = case_labels) +
  scale_linetype_manual(values = line_types, guide = "none") +
  scale_size_continuous(range = c(1.5, 3.4), breaks = grid[c(1, ceiling(length(grid) / 2), length(grid))]) +
  scale_x_continuous(labels = scales::label_percent(accuracy = 1), breaks = scales::pretty_breaks(4)) +
  scale_y_continuous(labels = scales::label_number(), expand = expansion(mult = c(0.04, 0.08))) +
  labs(
    title = if (has_points) {
      "SIFT1M: measured cost-arbitration points over matched reference curves"
    } else "SIFT1M: matched H1 / hierarchical-posting comparison",
    subtitle = sprintf(
      "Buffered IO | top-k %d | MaxCheck %d | posting pages %d\n%d warmup + %d measured queries, offset %d | NUMA %d/%d | %d ordinary repetitions",
      protocol$topk, protocol$maxcheck, protocol$page_limit, protocol$warmup,
      protocol$query_count, protocol$measure_offset, protocol$numa_cpu_node,
      protocol$numa_memory_node, protocol$repetitions
    ),
    x = "Recall@10", y = "Queries per second",
    color = "Navigation", shape = "Navigation", linetype = "Navigation", size = "nprobe",
    caption = paste(
      if (has_points) {
        "Same index data and ordinary timed body. Frozen curves retained; new policy shown only as separate points."
      } else "Same index data and ordinary timed body. Historical curves are excluded.",
      "QPS = 1000 / mean latency (ms); bars show ordinary-run min/max, not confidence intervals.",
      sprintf(
        "Curve supplier: eligible/physical degree < %.2f, physical degree >= %d. Lines follow nprobe order.",
        protocol$retained_ratio, protocol$minimum_physical_degree
      ),
      if (has_points) paste(
        point_input$label,
        ": independent measured points only, NOT joined to curves. No new sweeps.",
        paste0("Measured probes: ", point_probe_note, ".")
      ) else NULL,
      sep = "\n"
    )
  ) +
  theme_bw(base_size = 10) +
  theme(
    legend.position = "bottom", legend.box = "vertical",
    panel.grid.minor = element_blank(), strip.text = element_text(face = "bold"),
    plot.caption = element_text(hjust = 0), plot.title = element_text(face = "bold")
  ) +
  guides(color = guide_legend(ncol = if (has_points) 3 else 4),
         shape = guide_legend(ncol = if (has_points) 3 else 4))
ggsave(paste0(output, ".png"), chart, width = 14, height = if (has_points) 10 else 9, dpi = 180)
ggsave(paste0(output, ".pdf"), chart, width = 14, height = if (has_points) 10 else 9)
export <- rows[c(required_rows, "qps_trial_min", "qps_trial_max")]
export$measurement_kind <- "curve"
export$source_case <- rows$case
export$source_file <- normalizePath(args[[1]], mustWork = TRUE)
if (has_points) {
  point_export <- points[intersect(names(points), names(export))]
  for (column in setdiff(names(export), names(point_export))) point_export[[column]] <- NA
  point_export$index_fingerprint <- point_input$common$index_fingerprint
  point_export$measurement_kind <- "single_point"
  point_export$source_file <- normalizePath(points_path, mustWork = TRUE)
  export <- rbind(export, point_export[names(export)])
}
write.csv(export, paste0(output, ".plot-data.csv"), row.names = FALSE)
source_identity <- function(path) {
  list(path = normalizePath(path, mustWork = TRUE), md5 = unname(tools::md5sum(path)))
}
write_json(
  list(
    comparison = if (has_points) "matched_curves_with_separate_points" else "matched_only",
    historical_points = 0, total_points = nrow(export), curve_points = nrow(rows),
    summary = source_identity(args[[1]]), protocol_source = source_identity(args[[2]]),
    protocol = protocol, measured_scenarios = as.list(present),
    missing_scenarios = as.list(missing), partial_scenarios = length(missing) > 0,
    case_labels = as.list(case_labels),
    single_points = if (has_points) list(
      source = source_identity(points_path), count = nrow(points),
      label = point_input$label, connected = FALSE,
      scenarios = as.list(point_scenarios),
      common = point_input$common, provenance = point_input$provenance
    ) else NULL,
    aggregation = "1000 / mean ordinary latency_ms; min/max run bars, not confidence intervals"
  ),
  paste0(output, ".plot-provenance.json"), auto_unbox = TRUE, pretty = TRUE
)

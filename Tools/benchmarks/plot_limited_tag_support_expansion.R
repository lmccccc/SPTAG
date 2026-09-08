#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop(
    "usage: plot_limited_tag_support_expansion.R <final-summary-dir> <output-prefix>"
  )
}

suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
  library(grid)
})

summary_dir <- normalizePath(args[[1]], mustWork = TRUE)
output_prefix <- args[[2]]

bench_path <- file.path(summary_dir, "benchmark_summary.csv")
tags_path <- file.path(summary_dir, "audit_tags.csv")
structure_path <- file.path(summary_dir, "structure_summary.csv")
attribution_path <- file.path(summary_dir, "attribution_checks.json")

for (path in c(bench_path, tags_path, structure_path, attribution_path)) {
  if (!file.exists(path)) {
    stop(sprintf("missing summary artifact: %s", path))
  }
}

bench <- read.csv(bench_path, stringsAsFactors = FALSE)
tags <- read.csv(tags_path, stringsAsFactors = FALSE)
structure <- read.csv(structure_path, stringsAsFactors = FALSE)
attribution <- fromJSON(attribution_path, simplifyVector = TRUE)
is_independent_control <- identical(
  attribution$comparison_classification,
  "independent_rebuild_no_overflow_control"
)

if (nrow(bench) == 0L) stop("benchmark summary is empty")
if (nrow(tags) == 0L) stop("audit tag summary is empty")
if (nrow(structure) == 0L) stop("structure summary is empty")

label_workload <- function(values) {
  lookup <- c(
    unfilter = "unfiltered",
    broad_tag = "broad tag 0",
    bin13_20 = "per-query tags: 13-20 vectors",
    bin21_50 = "per-query tags: 21-50 vectors",
    bin51_200 = "per-query tags: 51-200 vectors",
    extreme_tag = "extreme sparse tag",
    medium_tag = "medium tag",
    numeric = "numeric DNF",
    mixed_dnf = "mixed DNF"
  )
  output <- values
  matched <- values %in% names(lookup)
  output[matched] <- unname(lookup[values[matched]])
  output
}

label_variant <- function(values) {
  lookup <- c(
    legacy_fixed_f1 = "legacy floor=1",
    legacy_active_baseline = "legacy active baseline",
    o_source_f1 = "O-source floor=1",
    o_source_f16 = "O-source floor=16"
  )
  output <- values
  matched <- values %in% names(lookup)
  output[matched] <- unname(lookup[values[matched]])
  output
}

make_palette <- function(values) {
  unique_values <- unique(values)
  colors <- grDevices::hcl.colors(length(unique_values), "Dark 3")
  stats::setNames(colors, unique_values)
}

metric_palette <- c(
  "own_heads" = "#2166AC",
  "support_heads" = "#1B9E77",
  "effective_heads" = "#762A83",
  "required_heads" = "#B2182B"
)

draw_three_panel <- function(title, top_plot, middle_plot, bottom_plot) {
  grid.newpage()
  pushViewport(viewport(layout = grid.layout(
    nrow = 4,
    ncol = 1,
    heights = unit.c(
      unit(0.07, "npc"),
      unit(0.34, "npc"),
      unit(0.25, "npc"),
      unit(0.34, "npc")
    )
  )))
  pushViewport(viewport(layout.pos.row = 1, layout.pos.col = 1))
  grid.text(title, gp = gpar(fontface = "bold", fontsize = 10))
  popViewport()
  pushViewport(viewport(layout.pos.row = 2, layout.pos.col = 1))
  print(top_plot, newpage = FALSE)
  popViewport()
  pushViewport(viewport(layout.pos.row = 3, layout.pos.col = 1))
  print(middle_plot, newpage = FALSE)
  popViewport()
  pushViewport(viewport(layout.pos.row = 4, layout.pos.col = 1))
  print(bottom_plot, newpage = FALSE)
  popViewport()
  popViewport()
}

fixtures <- unique(bench$fixture)

for (fixture in fixtures) {
  bench_fixture <- bench[bench$fixture == fixture, , drop = FALSE]
  tags_fixture <- tags[tags$fixture == fixture, , drop = FALSE]
  structure_fixture <- structure[structure$fixture == fixture, , drop = FALSE]
  if (nrow(bench_fixture) == 0L) next

  prefix <- output_prefix
  if (length(fixtures) > 1L) {
    safe_fixture <- gsub("[^A-Za-z0-9._-]+", "_", fixture)
    prefix <- paste0(output_prefix, "_", safe_fixture)
  }

  bench_fixture$workload_label <- label_workload(bench_fixture$workload)
  bench_fixture$variant_label <- label_variant(bench_fixture$variant)
  bench_fixture <- bench_fixture[order(
    bench_fixture$workload_label,
    bench_fixture$variant_label,
    bench_fixture$nprobe
  ), , drop = FALSE]

  variant_levels <- unique(bench_fixture$variant)
  variant_colors <- make_palette(variant_levels)
  names(variant_colors) <- variant_levels
  variant_shapes <- stats::setNames(c(16, 17, 15, 18, 8, 7)[seq_along(variant_levels)], variant_levels)

  perf_caption <- c(
    sprintf("Point: median of %s serial interleaved trials; vertical span: min-max QPS.",
            paste(sort(unique(bench_fixture$trial_count)), collapse = "/")),
    "Workloads use 100 warm-up queries, offset 100, and 900 measured queries from the first 1000 SIFT queries.",
    "Support floor is a planning target for retained O-derived support; actual effective nonempty H coverage can still remain below the floor after retained-membership RNG.",
    "Dedicated extreme-sparse routing is compiled out here; copied INI EST flags do not imply a separate active sparse route."
  )
  if (length(attribution$notes) > 0L) {
    perf_caption <- c(perf_caption, paste("Checks:", paste(attribution$notes, collapse = " ")))
  }
  if (is_independent_control) {
    perf_caption <- c(
      perf_caption,
      "This figure is an independent-rebuild no-overflow control; do not claim exact O identity unless H-source hashes and original-membership fingerprints both match.",
      "E=0 here is still not a no-op: unified fallback / RNG replica behavior can change scan volume, dedup, page reads, recall, and QPS."
    )
  }

  performance_plot <- ggplot(
    bench_fixture,
    aes(
      x = recall_median,
      y = qps_median,
      color = variant,
      shape = variant,
      group = variant
    )
  ) +
    geom_segment(
      aes(
        x = recall_median,
        xend = recall_median,
        y = qps_min,
        yend = qps_max
      ),
      alpha = 0.4,
      linewidth = 0.45
    ) +
    geom_path(linewidth = 0.65) +
    geom_point(aes(size = nprobe), alpha = 0.9) +
    facet_wrap(~ workload_label, scales = "free_y", ncol = 3) +
    scale_color_manual(values = variant_colors, labels = label_variant(names(variant_colors))) +
    scale_shape_manual(values = variant_shapes, labels = label_variant(names(variant_shapes))) +
    scale_size_continuous(breaks = sort(unique(bench_fixture$nprobe)), range = c(2.0, 5.5)) +
    labs(
      title = sprintf("Limited-tag support expansion performance: %s", fixture),
      subtitle = "Recall@10 vs median QPS; all variants and all nprobe points shown",
      x = "Recall@10",
      y = "Median QPS",
      color = "Variant",
      shape = "Variant",
      size = "nprobe",
      caption = paste(unlist(lapply(perf_caption, strwrap, width = 145)), collapse = "\n")
    ) +
    coord_cartesian(xlim = c(max(0, min(bench_fixture$recall_min) - 0.02), 1.0)) +
    theme_bw(base_size = 9) +
    theme(
      panel.grid.minor = element_blank(),
      plot.title = element_text(face = "bold"),
      plot.caption = element_text(hjust = 0, size = 7.5),
      legend.position = "bottom"
    )

  low_tags <- tags_fixture[tags_fixture$vectors <= 200, , drop = FALSE]
  if (nrow(low_tags) == 0L) {
    low_tags <- tags_fixture
  }
  low_tags$variant_label <- label_variant(low_tags$variant)

  coverage_long <- rbind(
    data.frame(
      fixture = low_tags$fixture,
      variant = low_tags$variant,
      variant_label = low_tags$variant_label,
      vectors = low_tags$vectors,
      heads = low_tags$own_heads,
      metric = "own_heads"
    ),
    data.frame(
      fixture = low_tags$fixture,
      variant = low_tags$variant,
      variant_label = low_tags$variant_label,
      vectors = low_tags$vectors,
      heads = low_tags$support_heads,
      metric = "support_heads"
    ),
    data.frame(
      fixture = low_tags$fixture,
      variant = low_tags$variant,
      variant_label = low_tags$variant_label,
      vectors = low_tags$vectors,
      heads = low_tags$effective_heads,
      metric = "effective_heads"
    ),
    data.frame(
      fixture = low_tags$fixture,
      variant = low_tags$variant,
      variant_label = low_tags$variant_label,
      vectors = low_tags$vectors,
      heads = low_tags$required_heads,
      metric = "required_heads"
    )
  )
  coverage_long$metric_label <- c(
    own_heads = "own H heads",
    support_heads = "support heads",
    effective_heads = "effective H heads",
    required_heads = "required heads"
  )[coverage_long$metric]
  coverage_means <- aggregate(
    heads ~ variant_label + vectors + metric,
    data = coverage_long,
    FUN = mean
  )

  coverage_plot <- ggplot(
    coverage_long,
    aes(x = vectors, y = heads, color = metric, group = metric)
  ) +
    geom_point(alpha = 0.08, size = 0.5) +
    geom_line(data = coverage_means, linewidth = 0.7) +
    facet_wrap(~ variant_label, ncol = min(3L, length(unique(coverage_long$variant_label)))) +
    scale_y_continuous(trans = "log1p", breaks = c(0, 1, 4, 16, 64, 256, 1024)) +
    scale_color_manual(values = metric_palette, labels = c(
      own_heads = "own H heads",
      support_heads = "support heads",
      effective_heads = "effective H heads",
      required_heads = "required heads"
    )) +
    labs(
      x = "Tag vectors (low-count tags only)",
      y = "Head counts (log1p scale)",
      color = "Metric",
      title = "Registered support vs actual H/own coverage",
      subtitle = "Points are tags; lines are means at each exact tag size (not fitted curves)"
    ) +
    theme_bw(base_size = 9) +
    theme(
      panel.grid.minor = element_blank(),
      legend.position = "bottom",
      plot.title = element_text(face = "bold", size = 10)
    )

  realization_plot <- ggplot(
    low_tags,
    aes(
      x = extra_support_heads,
      y = extra_h_postings,
      color = variant,
      shape = variant
    )
  ) +
    geom_abline(intercept = 0, slope = 1, linetype = "dashed", color = "grey50") +
    geom_count(alpha = 0.6) +
    scale_size_area(max_size = 5) +
    facet_wrap(~ variant_label, ncol = min(3L, length(unique(low_tags$variant_label)))) +
    scale_color_manual(values = variant_colors, labels = label_variant(names(variant_colors))) +
    scale_shape_manual(values = variant_shapes, labels = label_variant(names(variant_shapes))) +
    labs(
      x = "Registered extra supports",
      y = "Extra supports with nonempty H",
      color = "Variant",
      shape = "Variant",
      title = "Do the added supports receive H records?",
      subtitle = "Below the diagonal: some registered extra supports remain unused after RNG"
    ) +
    theme_bw(base_size = 9) +
    theme(
      panel.grid.minor = element_blank(),
      legend.position = "none",
      plot.title = element_text(face = "bold", size = 10)
    )

  structure_fixture$variant_label <- label_variant(structure_fixture$variant)
  overhead_long <- rbind(
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Real H1 heads (thousands)",
      value = structure_fixture$heads / 1000
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "H replicas / non-head vector",
      value = structure_fixture$h_replicas_per_nonhead
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "H + O payload (GiB)",
      value = structure_fixture$payload_bytes / (1024 ^ 3)
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Support file (MiB, not RSS)",
      value = structure_fixture$support_file_bytes / (1024 ^ 2)
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Registered extra supports",
      value = structure_fixture$extra_supports
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Unused extra supports",
      value = structure_fixture$unused_extra_supports
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Tags below feasible target (%)",
      value = 100 * structure_fixture$below_required_tags / structure_fixture$tag_count
    ),
    data.frame(
      variant_label = structure_fixture$variant_label,
      metric = "Max expanded H payload pages",
      value = structure_fixture$max_expanded_pure_pages
    )
  )

  overhead_plot <- ggplot(
    overhead_long,
    aes(x = variant_label, y = value, fill = variant_label)
  ) +
    geom_col(width = 0.72) +
    facet_wrap(~ metric, scales = "free_y", ncol = 4) +
    labs(
      x = NULL,
      y = NULL,
      fill = "Variant",
      title = "Index-size and support-structure overhead"
    ) +
    theme_bw(base_size = 9) +
    theme(
      panel.grid.minor = element_blank(),
      axis.text.x = element_text(angle = 30, hjust = 1, size = 7),
      legend.position = "none",
      plot.title = element_text(face = "bold", size = 10)
    )

  structure_title <- paste0(
    "Limited-tag support expansion structure: ", fixture,
    "\nThe floor is a support-planning target; actual H/own coverage is measured separately.",
    if (is_independent_control) "\n" else "\nReal heads and O are controlled. ",
    "Metadata sizes shown are serialized bytes, not process RSS."
  )
  if (is_independent_control) {
    structure_title <- paste0(
      structure_title,
      "\nThis fixture is an independent-rebuild no-overflow control, not an exact-O-identity claim.",
      "\nE=0 still does not guarantee a no-op because unified fallback / RNG replicas can alter observed search behavior."
    )
  }

  ggsave(
    filename = paste0(prefix, "_performance.pdf"),
    plot = performance_plot,
    width = 11,
    height = 7
  )
  ggsave(
    filename = paste0(prefix, "_performance.png"),
    plot = performance_plot,
    width = 11,
    height = 7,
    dpi = 180
  )

  grDevices::pdf(paste0(prefix, "_structure.pdf"), width = 11, height = 13)
  draw_three_panel(structure_title, coverage_plot, realization_plot, overhead_plot)
  grDevices::dev.off()
  grDevices::png(
    paste0(prefix, "_structure.png"),
    width = 1980,
    height = 2340,
    res = 180
  )
  draw_three_panel(structure_title, coverage_plot, realization_plot, overhead_plot)
  grDevices::dev.off()
}

#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("usage: plot_sift1m_h1_h2_curve.R <results.jsonl> <output-prefix>")
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

rows$mode <- factor(rows$mode, levels = c("H1Only", "H2Only"))
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

make_workload_plot <- function(workload_id, full_recall_range) {
  workload_rows <- rows[
    rows$workload == unname(workload_labels[workload_id]),
  ]
  x_breaks <- if (full_recall_range) {
    c(0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 1.0)
  } else {
    c(0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
  }
  x_limits <- if (full_recall_range) c(0, 1) else c(0.6, 1)

  ggplot(workload_rows, aes(
    x = recall, y = qps, color = mode, shape = mode, group = mode
  )) +
    geom_path(linewidth = 0.65) +
    geom_point(aes(size = nprobe), alpha = 0.9) +
    geom_vline(
      xintercept = c(0.90, 0.95), linetype = "dashed",
      color = "grey35", linewidth = 0.45
    ) +
    scale_color_manual(values = c(H1Only = "#2166AC", H2Only = "#B2182B")) +
    scale_size_continuous(range = c(1.8, 4.8), breaks = c(16, 32, 62, 96, 128, 256, 384)) +
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
    "SIFT1M H1-only vs H2-only head navigation",
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
  grid::popViewport()
}

grDevices::pdf(paste0(output, ".pdf"), width = 11, height = 7)
draw_combined_plot()
grDevices::dev.off()
grDevices::png(paste0(output, ".png"), width = 1980, height = 1260, res = 180)
draw_combined_plot()
grDevices::dev.off()

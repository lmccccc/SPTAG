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
  broad_tag = "broad tag (selectivity: 17.0125%)",
  medium_tag = "medium tag (selectivity: 1.7012%)",
  sparse_tag = "sparse tag (selectivity: 0.0851%)",
  numeric = "numeric (selectivity: 1%)",
  mixed_dnf = "mixed DNF (selectivity: 0.1291%)"
)
rows$workload <- factor(
  rows$workload,
  levels = names(workload_labels),
  labels = unname(workload_labels)
)

plot <- ggplot(
  rows,
  aes(
    x = recall,
    y = qps,
    color = mode,
    shape = mode,
    group = mode
  )
) +
  geom_path(linewidth = 0.65) +
  geom_point(aes(size = nprobe), alpha = 0.9) +
  geom_vline(
    xintercept = c(0.90, 0.95),
    linetype = "dashed",
    color = "grey35",
    linewidth = 0.45
  ) +
  facet_wrap(~ workload, scales = "free_y", ncol = 3) +
  scale_color_manual(values = c(H1Only = "#2166AC", H2Only = "#B2182B")) +
  scale_size_continuous(range = c(1.8, 4.8), breaks = c(16, 32, 62, 96, 128)) +
  scale_x_continuous(
    breaks = c(0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
  ) +
  coord_cartesian(xlim = c(0.6, 1)) +
  labs(
    x = "Recall@10",
    y = "Queries per second",
    color = "Navigation",
    shape = "Navigation",
    size = "nprobe",
    title = "SIFT1M H1-only vs H2-only head navigation"
  ) +
  theme_bw(base_size = 10) +
  theme(
    legend.position = "bottom",
    panel.grid.minor = element_blank(),
    strip.background = element_rect(fill = "grey95")
  )

ggsave(paste0(output, ".pdf"), plot, width = 11, height = 7)
ggsave(paste0(output, ".png"), plot, width = 11, height = 7, dpi = 180)

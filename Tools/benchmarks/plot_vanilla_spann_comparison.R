#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("usage: plot_vanilla_spann_comparison.R <curve.csv> <new-output-directory>")
}
suppressPackageStartupMessages(library(ggplot2))
rows <- read.csv(args[[1]], stringsAsFactors = FALSE)
required <- c("engine", "nprobe", "recall", "median_qps", "head_ms", "posting_ms")
if (!all(required %in% names(rows)) ||
    !setequal(rows$engine, c("vanilla", "h3")) ||
    anyDuplicated(rows[c("engine", "nprobe")]) ||
    any(!is.finite(as.matrix(rows[required[-1]]))) ||
    any(rows$recall < 0 | rows$recall > 1) ||
    any(rows$median_qps <= 0 | rows$head_ms < 0 | rows$posting_ms < 0)) {
  stop("require valid measured vanilla and H3 rows; no missing phase estimates")
}
out <- args[[2]]
if (!dir.exists(out)) dir.create(out, recursive = TRUE)
prefixes <- c("recall_qps", "navigation_posting")
paths <- unlist(lapply(prefixes, function(name) {
  file.path(out, paste0(name, c(".png", ".pdf")))
}))
if (any(file.exists(paths))) stop("refusing to overwrite an existing comparison figure")

labels <- c(vanilla = "Microsoft SPANN (2ac3ebc)", h3 = "Preserved H3")
colors <- c(vanilla = "#1f77b4", h3 = "#d95f02")
common <- list(
  scale_color_manual(values = colors, labels = labels, name = NULL),
  scale_x_continuous(labels = function(x) sprintf("%.0f%%", 100 * x)),
  theme_bw(base_size = 12),
  theme(legend.position = "bottom", panel.grid.minor = element_blank())
)
qps <- ggplot(rows, aes(recall, median_qps, color = engine)) +
  geom_line(linewidth = 0.7) + geom_point(size = 2) +
  geom_text(aes(label = nprobe, vjust = ifelse(engine == "h3", 1.6, -0.7)),
            size = 3, show.legend = FALSE) +
  common +
  scale_y_continuous(expand = expansion(mult = c(0.10, 0.12))) +
  labs(
    title = "SIFT1M: original SPANN vs. three-level hierarchy",
    subtitle = "Direct IO; 1,000 warmup + 1,000 measured; one thread; NUMA 2",
    x = "Recall@10", y = "QPS (median of three ordinary runs)",
    caption = "Point labels: native nprobe. Different head sets and posting layouts; not a shared-index ablation."
  )

phases <- rbind(
  transform(rows, phase = "Head navigation", milliseconds = head_ms),
  transform(rows, phase = "Posting access + scanning", milliseconds = posting_ms)
)
phases$phase <- factor(phases$phase, levels = c("Head navigation", "Posting access + scanning"))
cost <- ggplot(phases, aes(recall, milliseconds, color = engine)) +
  geom_line(linewidth = 0.7) + geom_point(size = 2) +
  common + facet_wrap(~phase, scales = "free_y", nrow = 1) +
  labs(
    title = "Search cost at achieved recall",
    subtitle = "Native profiled phases; ordinary QPS is measured separately",
    x = "Recall@10", y = "Mean time per query (ms)",
    caption = "Original SPANN exposes combined posting time; no device-only latency is inferred."
  )
for (extension in c("png", "pdf")) {
  ggsave(file.path(out, paste0("recall_qps.", extension)),
         qps, width = 8.5, height = 5.2, dpi = 180)
  ggsave(file.path(out, paste0("navigation_posting.", extension)),
         cost, width = 10, height = 4.8, dpi = 180)
}

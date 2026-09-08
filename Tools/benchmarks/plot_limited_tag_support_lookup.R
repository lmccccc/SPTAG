#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) {
  stop(paste(
    "usage: plot_limited_tag_support_lookup.R",
    "<uninstrumented-summary.csv> <phase-summary.csv> <output-prefix>"
  ))
}

suppressPackageStartupMessages(library(ggplot2))

bench <- read.csv(normalizePath(args[[1]], mustWork = TRUE))
phases <- read.csv(normalizePath(args[[2]], mustWork = TRUE))
prefix <- args[[3]]
keys <- c("workload", "variant", "revision", "nprobe")
required <- c(keys, "trials", "qps_median", "qps_min", "qps_max", "recall")
for (data in list(bench, phases)) {
  if (nrow(data) == 0L || !all(required %in% names(data))) {
    stop("missing benchmark rows or columns")
  }
  if (anyDuplicated(data[keys]) || anyNA(data[required])) {
    stop("duplicate or incomplete benchmark rows")
  }
  if (!setequal(data$revision, c("before", "after")) ||
      any(data$qps_median <= 0) || any(data$nprobe <= 0)) {
    stop("expected positive measurements for both binary revisions")
  }
}
if (!"phase_h2Tag" %in% names(phases) ||
    any(!is.finite(phases$phase_h2Tag)) || any(phases$phase_h2Tag < 0)) {
  stop("missing native hierarchy tag-admission times")
}
if ("phase_h2Tag" %in% names(bench)) {
  stop("the performance input must not contain instrumented QPS")
}

workload_labels <- c(
  unfilter = "Unfiltered",
  broad_tag = "Broad tag 0",
  bin13_20 = "Tags: 13-20 vectors",
  bin21_50 = "Tags: 21-50 vectors",
  bin51_200 = "Tags: 51-200 vectors"
)
floor_labels <- c(o_source_f1 = "Floor 1", o_source_f16 = "Floor 16")
label_rows <- function(data) {
  if (any(!data$workload %in% names(workload_labels)) ||
      any(!data$variant %in% names(floor_labels))) {
    stop("unexpected workload or support floor")
  }
  data$workload_label <- factor(
    data$workload, levels = names(workload_labels), labels = workload_labels
  )
  data$floor <- factor(
    data$variant, levels = names(floor_labels), labels = floor_labels
  )
  data
}

pair_keys <- c("workload", "variant", "nprobe")
paired <- merge(
  bench[bench$revision == "before", ],
  bench[bench$revision == "after", ],
  by = pair_keys, suffixes = c("_before", "_after"), all = TRUE
)
if (nrow(paired) * 2L != nrow(bench) || anyNA(paired) ||
    any(paired$recall_before != paired$recall_after)) {
  stop("unpaired measurements or changed recall")
}
paired$gain <- 100 * (paired$qps_median_after / paired$qps_median_before - 1)
bench <- label_rows(bench)
paired <- label_rows(paired)
phases <- label_rows(phases)
bench$binary <- factor(
  bench$revision, levels = c("before", "after"),
  labels = c("Original lookup", "Contiguous rows")
)
phases$binary <- factor(
  phases$revision, levels = c("before", "after"),
  labels = levels(bench$binary)
)

palette <- c("Floor 1" = "#2166AC", "Floor 16" = "#D95F02")
caption <- paste(
  sprintf("Median of %s balanced serial trials; CPU 24, one native search thread.",
          paste(sort(unique(bench$trials)), collapse = "/")),
  "Identical saved indexes and native INIs; 100 warm-up + 900 measured queries.",
  "Original publication is preserved; these figures isolate the runtime lookup correction.",
  sep = "\n"
)
theme_set(theme_bw(base_size = 10))

performance <- ggplot(
  bench, aes(nprobe, qps_median, color = floor, linetype = binary, shape = binary,
             group = interaction(floor, binary))
) +
  geom_linerange(aes(ymin = qps_min, ymax = qps_max), alpha = 0.4) +
  geom_line(linewidth = 0.65) +
  geom_point(size = 2.4) +
  facet_wrap(~ workload_label, scales = "free_y", ncol = 3) +
  scale_x_continuous(trans = "log2", breaks = sort(unique(bench$nprobe))) +
  scale_color_manual(values = palette) +
  scale_linetype_manual(values = c("dashed", "solid")) +
  scale_shape_manual(values = c(1, 16)) +
  labs(
    title = "Contiguous H1 support lookup: same-index binary A/B",
    subtitle = "SIFT1M / 8,192 tags; all measured workloads and probes",
    x = "nprobe", y = "Median QPS", color = "Support floor",
    linetype = "Binary", shape = "Binary",
    caption = paste(caption, "Vertical spans are trial min-max, not confidence intervals.",
                    sep = "\n")
  ) +
  theme(legend.position = "bottom")

speedup <- ggplot(paired, aes(nprobe, gain, color = floor, group = floor)) +
  geom_hline(yintercept = 0, linetype = "dashed", color = "grey45") +
  geom_line(linewidth = 0.65) +
  geom_point(size = 2.4) +
  facet_wrap(~ workload_label, ncol = 3) +
  scale_x_continuous(trans = "log2", breaks = sort(unique(bench$nprobe))) +
  scale_y_continuous(labels = function(x) sprintf("%+.0f%%", x)) +
  scale_color_manual(values = palette) +
  labs(
    title = "Lookup correction: QPS change against each floor's own baseline",
    subtitle = "Negative points are retained; improvement is not inferred from the floor-to-floor gap",
    x = "nprobe", y = "Change in median QPS", color = "Support floor",
    caption = caption
  ) +
  theme(legend.position = "bottom")

admission <- ggplot(
  phases, aes(floor, 1000 * phase_h2Tag, fill = binary)
) +
  geom_col(position = position_dodge(width = 0.8), width = 0.7) +
  geom_text(
    aes(label = sprintf("%.1f", 1000 * phase_h2Tag)),
    position = position_dodge(width = 0.8), vjust = -0.4, size = 3.5
  ) +
  facet_wrap(~ workload_label, scales = "free_y", ncol = 2) +
  scale_fill_manual(values = c("#999999", "#1B9E77")) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.13))) +
  labs(
    title = "Native hierarchy tag-admission time: diagnostic only",
    subtitle = sprintf("nprobe %s; medians of per-process mean phase times",
                       paste(sort(unique(phases$nprobe)), collapse = "/")),
    x = "Support floor", y = "Tag-admission time (us/query)", fill = "Binary",
    caption = paste(
      "Native INI LogPhaseTime=true; instrumented QPS is excluded from the performance figures.",
      "Phase means exclude the 100 warm-up queries; each uses 900 measured queries.",
      "This phase includes hierarchy admission, not only individual H1 tag comparisons.",
      sep = "\n"
    )
  ) +
  theme(legend.position = "bottom")

plots <- list(performance = performance, speedup = speedup, admission = admission)
outputs <- paste0(prefix, "_", rep(names(plots), each = 2), rep(c(".png", ".pdf"), 3))
if (any(file.exists(outputs))) stop("refusing to overwrite existing figure files")
if (!dir.exists(dirname(prefix))) {
  if (!dir.create(dirname(prefix), recursive = TRUE)) stop("cannot create figure directory")
}
for (name in names(plots)) {
  for (format in c("png", "pdf")) {
    output <- paste0(prefix, "_", name, ".", format)
    ggsave(output, plot = plots[[name]], width = 12, height = 7, dpi = 180)
    message(output)
  }
}

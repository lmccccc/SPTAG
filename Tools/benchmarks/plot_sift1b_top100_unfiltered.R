suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
root <- normalizePath(args[[1]], mustWork = TRUE)
output <- file.path(root, "plots")
stopifnot(!file.exists(output))
data <- read.csv(file.path(root, "summary.csv"), stringsAsFactors = FALSE)
analysis <- fromJSON(file.path(root, "analysis.json"))
algorithms <- c("SPTAG", "PipeANN", "Filtered_DiskANN")
scenarios <- c("unfilter", "broad_tag", "medium_tag", "sel_01pct")
stopifnot(nrow(data) == analysis$aggregate_points, all(data$topk == 100),
          all(data$threads == 1), all(data$queries == 1000), all(data$repeats == 2),
          all(is.finite(data$qps) & data$qps > 0),
          all(is.finite(data$recall) & data$recall >= 0 & data$recall <= 1),
          !any(data$setting == "control"),
          setequal(unique(data$engine), algorithms), setequal(unique(data$scenario), scenarios),
          isTRUE(analysis$previous_filtering_rows_identical),
          isTRUE(analysis$previous_measurements_identical))
for (i in seq_len(nrow(analysis$coverage))) {
  entry <- analysis$coverage[i, ]
  stopifnot(sum(data$scenario == entry$scenario & data$engine == entry$engine) == entry$points)
}
dominates <- function(a, b) {
  a$recall >= b$recall & a$qps >= b$qps & (a$recall > b$recall | a$qps > b$qps)
}
data$algorithm_frontier <- tolower(as.character(data$algorithm_frontier)) == "true"
data$joint_frontier <- tolower(as.character(data$joint_frontier)) == "true"
for (i in seq_len(nrow(data))) {
  peers <- data[data$scenario == data$scenario[i], ]
  stopifnot(data$algorithm_frontier[i] == !any(dominates(peers[peers$engine == data$engine[i], ], data[i, ])),
            data$joint_frontier[i] == !any(dominates(peers, data[i, ])))
}
data$engine <- factor(data$engine, levels = algorithms)
data$scenario <- factor(data$scenario, levels = scenarios)
data$recall_percent <- data$recall * 100
profiles <- c(base = "Base search", L_sweep = "Base search", wide = "Original wide",
              graph16384 = "H1 MaxCheck=16384", graph32768 = "H1 MaxCheck=32768")
data$profile <- unname(profiles[data$setting])
stopifnot(!anyNA(data$profile))
labels <- c(unfilter = "Unfiltered (100%)", broad_tag = "Broad tag (17.012493%)",
            medium_tag = "Medium tag (1.7012493%)", sel_01pct = "Sparse tag (0.1000735%)")
colors <- c(SPTAG = "#009E73", PipeANN = "#0072B2", Filtered_DiskANN = "#E69F00")

render <- function(left, unfiltered_only) {
  shown <- if (unfiltered_only) data[data$scenario == "unfilter", ] else data
  front <- shown[shown$algorithm_frontier, ]
  front <- front[order(front$scenario, front$engine, front$recall), ]
  ggplot(shown, aes(x = recall_percent, y = qps, color = engine)) +
    geom_point(alpha = .20, size = 1.6) +
    geom_errorbar(aes(ymin = qps_min, ymax = qps_max), width = .14, alpha = .45) +
    geom_line(data = front, aes(group = engine), linewidth = .65, linetype = "dashed") +
    geom_point(data = front, aes(shape = profile), size = 2.8) +
    geom_point(data = shown[shown$joint_frontier, ], color = "black", shape = 21,
               fill = NA, size = 4.1, stroke = .7) +
    facet_wrap(~scenario, ncol = if (unfiltered_only) 1 else 2, labeller = as_labeller(labels)) +
    scale_color_manual(values = colors,
                       labels = c("SPTAG (V5)", "PipeANN", "DiskANN (categorical index)"), drop = FALSE) +
    scale_shape_manual(values = c("Base search" = 16, "Original wide" = 17,
                                  "H1 MaxCheck=16384" = 15, "H1 MaxCheck=32768" = 18)) +
    scale_y_log10() +
    coord_cartesian(xlim = c(left, 100)) +
    labs(title = if (unfiltered_only) "Unfiltered top100: native Recall-QPS frontiers" else
           "SIFT1B top100: unfiltered and filtering Recall-QPS frontiers",
         subtitle = "1 query worker | same 1,000 queries | two repetitions | no index rebuild",
         x = "Recall@100 (%)", y = "QPS (log scale)", color = NULL, shape = NULL,
         caption = paste(
           "Opaque points: per-engine Pareto frontier. Black rings: joint frontier. Faint points: dominated settings.",
           "Dashed lines guide the eye, not interpolated measurements. Bars retain both QPS repetitions.",
           "Unfiltered: native search without a predicate. PipeANN: matching 1% memory-entry index, mem_L=10.",
           "DiskANN: existing categorical R64/buildL1/FilteredL100/PQ32 index, not an unfiltered-tuned rebuild.",
           "SPTAG: buffered I/O, one initial warmup. Baselines: direct I/O, per-point warmup.",
           if (unfiltered_only) "All unfiltered points are fresh normal-client measurements; no diagnostic timings." else
             "Filtering results are reused unchanged; some broad high-recall QPS repetitions differ by 1.24-2.51x.",
           "Same-cohort parameter exploration; not held-out evaluation. No omitted rounds or timing normalization.",
           sep = "\n")) +
    theme_bw(base_size = 11) +
    theme(legend.position = "bottom", legend.box = "vertical",
          plot.title = element_text(face = "bold"),
          plot.caption = element_text(hjust = 0, size = 8),
          panel.grid.minor = element_blank(), strip.text = element_text(face = "bold"))
}

dir.create(output)
for (unfiltered_only in c(FALSE, TRUE)) {
  for (left in c(0, 70)) {
    name <- paste0(if (unfiltered_only) "unfiltered_recall100_qps" else "recall100_qps",
                   if (left == 70) "_zoom" else "")
    plot <- render(left, unfiltered_only)
    for (extension in c("png", "pdf")) {
      ggsave(file.path(output, paste0(name, ".", extension)), plot,
             width = if (unfiltered_only) 11 else 13.5,
             height = if (unfiltered_only) 7.2 else 10, dpi = 180)
    }
  }
}
write_json(list(topk = 100, points = nrow(data), input = file.path(root, "summary.csv"),
                input_md5 = unname(tools::md5sum(file.path(root, "summary.csv"))),
                unfiltered_points = sum(data$scenario == "unfilter"),
                no_interpolation = TRUE, previous_filtering_rows_identical = TRUE),
           file.path(output, "metadata.json"), auto_unbox = TRUE, pretty = TRUE)
cat("Rendered", nrow(data), "top100 settings including unfiltered to", output, "\n")

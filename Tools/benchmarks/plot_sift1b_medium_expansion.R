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
stopifnot(nrow(data) == analysis$aggregate_points, all(data$topk == 100),
          all(data$threads == 1), all(data$queries == 1000), all(data$repeats == 2),
          all(is.finite(data$qps) & data$qps > 0),
          all(is.finite(data$recall) & data$recall >= 0 & data$recall <= 1),
          !any(data$setting == "control"))
algorithms <- c("SPTAG", "PipeANN", "Filtered_DiskANN")
scenarios <- c("broad_tag", "medium_tag", "sel_01pct")
stopifnot(setequal(unique(data$engine), algorithms), setequal(unique(data$scenario), scenarios))
coverage <- table(data$scenario, data$engine)
stopifnot(all(coverage[, "SPTAG"] == 20L),
          all(coverage[, "PipeANN"] == 8L), all(coverage[, "Filtered_DiskANN"] == 8L))
dominates <- function(a, b) {
  a$recall >= b$recall & a$qps >= b$qps &
    (a$recall > b$recall | a$qps > b$qps)
}
data$algorithm_frontier <- tolower(as.character(data$algorithm_frontier)) == "true"
data$joint_frontier <- tolower(as.character(data$joint_frontier)) == "true"
for (i in seq_len(nrow(data))) {
  peers <- data[data$scenario == data$scenario[i], ]
  own <- peers[peers$engine == data$engine[i], ]
  stopifnot(data$algorithm_frontier[i] == !any(dominates(own, data[i, ])),
            data$joint_frontier[i] == !any(dominates(peers, data[i, ])))
}
data$engine <- factor(data$engine, levels = algorithms)
data$scenario <- factor(data$scenario, levels = scenarios)
data$recall_percent <- data$recall * 100
profiles <- c(base = "Base search", L_sweep = "Base search", wide = "Original wide",
              graph16384 = "H1 MaxCheck=16384", graph32768 = "H1 MaxCheck=32768")
data$profile <- unname(profiles[data$setting])
stopifnot(!anyNA(data$profile))
front <- data[data$algorithm_frontier, ]
joint <- data[data$joint_frontier, ]
front <- front[order(front$scenario, front$engine, front$recall), ]
labels <- c(broad_tag = "Broad tag (17.012493%)",
            medium_tag = "Medium tag (1.7012493%)",
            sel_01pct = "Sparse tag (0.1000735%)")
colors <- c(SPTAG = "#009E73", PipeANN = "#0072B2", Filtered_DiskANN = "#E69F00")

render <- function(left) {
  ggplot(data, aes(x = recall_percent, y = qps, color = engine)) +
    geom_point(alpha = .20, size = 1.6) +
    geom_errorbar(aes(ymin = qps_min, ymax = qps_max), width = .14, alpha = .45) +
    geom_line(data = front, aes(group = engine), linewidth = .65, linetype = "dashed") +
    geom_point(data = front, aes(shape = profile), size = 2.8) +
    geom_point(data = joint, color = "black", shape = 21, fill = NA, size = 4.1, stroke = .7) +
    facet_wrap(~scenario, nrow = 1, labeller = as_labeller(labels)) +
    scale_color_manual(values = colors,
                       labels = c("SPTAG (same V5 index)", "PipeANN", "Filtered DiskANN"), drop = FALSE) +
    scale_shape_manual(values = c("Base search" = 16, "Original wide" = 17,
                                  "H1 MaxCheck=16384" = 15, "H1 MaxCheck=32768" = 18)) +
    scale_y_log10() +
    coord_cartesian(xlim = c(left, 100)) +
    labs(title = "Top100: SPTAG parameter extension vs PipeANN and Filtered DiskANN",
         subtitle = "SIFT1B | 1 query worker | same 1,000 queries | two repetitions | prior comparison reused",
         x = "Recall@100 (%)", y = "QPS (log scale)", color = NULL, shape = NULL,
         caption = paste(
           "Opaque points: per-algorithm Pareto frontier. Black rings: joint frontier. Faint points: dominated settings.",
           "Dashed lines guide the eye, not interpolated measurements. Bars show QPS repetition ranges; diagnostic timings are excluded.",
           "SPTAG base: MaxCheck=2048, extra=2048, width=8. Original wide: MaxCheck=8192, extra=8192, width=32.",
           "New profiles: MaxCheck=16384 or 32768, extra=2048, width=8; both tested identically across all three labels.",
           "SPTAG: buffered I/O, one initial warmup. Baselines: direct I/O, per-point warmup; previous same-cohort results reused.",
           paste0("No index rebuild. Post-hoc parameter exploration, not held-out evaluation. Bracketing control QPS ratio: ",
                  sprintf("%.3f", analysis$control$qps_ratio), "; controls excluded from frontiers."),
           sep = "\n")) +
    theme_bw(base_size = 11) +
    theme(legend.position = "bottom", legend.box = "vertical",
          plot.title = element_text(face = "bold"),
          plot.caption = element_text(hjust = 0, size = 8),
          panel.grid.minor = element_blank(), strip.text = element_text(face = "bold"))
}

dir.create(output)
for (left in c(0, 70)) {
  name <- if (left == 0) "recall100_qps" else "recall100_qps_zoom"
  plot <- render(left)
  ggsave(file.path(output, paste0(name, ".png")), plot, width = 13.5, height = 6.1, dpi = 180)
  ggsave(file.path(output, paste0(name, ".pdf")), plot, width = 13.5, height = 6.1)
}
write_json(list(topk = 100, points = nrow(data), input = file.path(root, "summary.csv"),
                algorithm_frontier_points = nrow(front), joint_frontier_points = nrow(joint),
                no_interpolation = TRUE, controls_included = FALSE),
           file.path(output, "metadata.json"), auto_unbox = TRUE, pretty = TRUE)
cat("Rendered", nrow(data), "combined top100 operating points to", output, "\n")

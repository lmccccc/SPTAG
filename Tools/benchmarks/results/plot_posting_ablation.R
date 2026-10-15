suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
root <- normalizePath(args[[1]], mustWork = TRUE)
complete <- fromJSON(file.path(root, "completion.json"))
stopifnot(complete$state == "complete", complete$exit_code == 0,
          !file.exists(file.path(root, "failure.json")))
events <- fromJSON(file.path(root, "events.json"), simplifyVector = FALSE)
rows <- lapply(events, function(e) {
  p <- e$point
  s <- fromJSON(p$parameters)
  controls <- s
  controls$GlobalPostingSearch <- NULL
  controls$EnablePostingNavigation <- NULL
  mode <- if (s$GlobalPostingSearch == "true") "global" else
    if (s$EnablePostingNavigation == "true") "full" else "limited"
  stopifnot(e$kind == "measurement", p$cohort_queries == 1000, p$topk == 100,
            p$threads == 1, p$repeats == 3, p$complete_repeats_same_client,
            s$WarmupPolicy == "once_per_mode_predicate",
            all(vapply(e$measurements, function(r) !r$diagnostic, logical(1))))
  data.frame(scenario = p$scenario, mode = mode, nprobe = p$search_value,
             recall = p$recall, qps = p$qps, qps_min = p$qps_min, qps_max = p$qps_max,
             maxcheck = as.integer(s$MaxCheck), parameters = p$parameters,
             controls = as.character(toJSON(controls, auto_unbox = TRUE)),
             client = e$version$client, index = e$version$index)
})
d <- do.call(rbind, rows)
scenarios <- c("sel50", "sel20", "sel10", "sel5", "sel1", "sel01")
modes <- c("global", "limited", "full")
stopifnot(setequal(unique(d$scenario), scenarios), setequal(unique(d$mode), modes),
          length(unique(d$client)) == 1L, length(unique(d$index)) == 1L,
          all(is.finite(d$qps) & d$qps > 0 & d$recall >= 0 & d$recall <= 1),
          all(d$qps_min <= d$qps & d$qps <= d$qps_max),
          all(table(d$scenario, d$mode) >= 1),
          !anyDuplicated(d[c("scenario", "mode", "controls", "nprobe")]))
for (scenario in scenarios) {
  grids <- lapply(modes, function(mode) {
    selected <- d[d$scenario == scenario & d$mode == mode, c("controls", "nprobe")]
    selected <- selected[order(selected$controls, selected$nprobe), ]
    rownames(selected) <- NULL
    selected
  })
  stopifnot(identical(grids[[1]], grids[[2]]), identical(grids[[2]], grids[[3]]))
}
control_values <- sort(unique(d$controls))
d$control_id <- paste0("C", match(d$controls, control_values))
control_labels <- vapply(seq_along(control_values), function(i) {
  s <- fromJSON(control_values[[i]])
  sprintf("C%d: MC=%s, width=%s, rate=%s", i, s$MaxCheck,
          s$PostingNavigationWidth, s$PostingMatchRatePercent)
}, character(1))
output <- file.path(root, "plots")
stopifnot(!file.exists(output))
dir.create(output)
write.csv(d, file.path(output, "recall_qps.csv"), row.names = FALSE)
d$scenario <- factor(d$scenario, levels = scenarios,
  labels = c("49.829% matching", "19.848% matching", "10.053% matching",
             "4.993% matching", "1.001% matching", "0.100% matching"))
d$mode <- factor(d$mode, levels = modes)
d <- d[order(d$scenario, d$mode, d$control_id, d$nprobe), ]
p <- ggplot(d, aes(100 * recall, qps, color = mode)) +
  geom_errorbar(aes(ymin = qps_min, ymax = qps_max), width = .2, alpha = .5) +
  geom_path(aes(group = interaction(mode, control_id), linetype = control_id), linewidth = .6) +
  geom_point(size = 1.8) +
  facet_wrap(~scenario, ncol = 3, scales = "free_y") +
  scale_y_log10() +
  scale_color_manual(values = c(global = "#D55E00", limited = "#0072B2", full = "#009E73"),
    labels = c("Global O, no hierarchy", "Limited H, no hierarchy", "Full H + hierarchy")) +
  scale_linetype_manual(values = setNames(rep(c("solid", "dashed", "dotted", "dotdash", "longdash", "twodash", "1343"),
                                            length.out = length(control_values)), paste0("C", seq_along(control_values))),
                        breaks = paste0("C", seq_along(control_values)), labels = control_labels) +
  labs(title = "SIFT1B: posting-path ablations",
       subtitle = "Recall@100 | 1,000 queries | one worker | three repetitions | identical index and client",
       x = "Recall@100 (%)", y = "QPS (log scale)", color = NULL, linetype = "Original controls",
       caption = paste(
         "One full warmup per mode/predicate; no time floor. Points are measured QPS medians; bars are repeat ranges.",
         "Lines connect observed nprobe settings, not interpolated measurements. All points retained, including slow/underfilled ones.",
         "Each line keeps non-mode controls fixed; all three modes use the same original parameter points, without pilot tuning.",
         "Global-to-limited changes head admission and SSD region, not just byte layout. Limited-to-full changes hierarchy activation only.",
         "Global O reuses the existing H1 geometry and H/O storage; it is not a separately rebuilt upstream SPANN index.",
         sep = "\n")) +
  theme_bw(base_size = 11) +
  guides(linetype = guide_legend(ncol = 2), color = guide_legend(nrow = 1)) +
  theme(legend.position = "bottom", plot.caption = element_text(hjust = 0, size = 8),
        panel.grid.minor = element_blank())
for (zoom in c(FALSE, TRUE)) {
  figure <- p + coord_cartesian(xlim = if (zoom) c(80, 100) else c(0, 100))
  name <- paste0("posting_ablation_recall_qps", if (zoom) "_80_100" else "")
  for (extension in c("png", "pdf"))
    ggsave(file.path(output, paste0(name, ".", extension)), figure,
           width = 14, height = 9, dpi = 180)
}
write_json(list(points = nrow(d), scenarios = 6, modes = 3, query_count = 1000,
  repeats = 3, threads = 1, no_interpolation = TRUE, same_index_client = TRUE,
  matched_parameter_grids = TRUE, control_groups = length(control_values),
  input_md5 = unname(tools::md5sum(file.path(root, "events.json")))),
  file.path(output, "metadata.json"), auto_unbox = TRUE, pretty = TRUE)

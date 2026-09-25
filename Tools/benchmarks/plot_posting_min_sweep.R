#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(jsonlite))
args <- commandArgs(trailingOnly = TRUE)
usage <- paste("Usage: Rscript plot_posting_min_sweep.R <campaign-root>",
               "<new-output-directory> [--partial]",
               "[--member-postfilter | --postgraph | --selectivity | --compact-storage]")
if (length(args) < 2L || length(args) > 4L) stop(usage)
flags <- args[-c(1L, 2L)]
comparison_flags <- c("--member-postfilter", "--postgraph", "--selectivity", "--compact-storage")
if (anyDuplicated(flags) ||
    any(!flags %in% c("--partial", comparison_flags)) ||
    sum(flags %in% comparison_flags) > 1L) {
  stop(usage)
}
partial <- "--partial" %in% flags
member_postfilter <- "--member-postfilter" %in% flags
postgraph <- "--postgraph" %in% flags
selectivity <- "--selectivity" %in% flags
postgraph_comparison <- postgraph || selectivity
compact_storage <- "--compact-storage" %in% flags
root <- normalizePath(args[[1]], mustWork = TRUE)
output <- args[[2]]
protocol_path <- file.path(root, "registration.json")
input_path <- file.path(root, "plain-results.json")
registration_md5 <- if (selectivity) as.character(tools::md5sum(protocol_path)) else NULL
check_registration <- function(created = character()) {
  if (selectivity &&
      !identical(registration_md5, as.character(tools::md5sum(protocol_path)))) {
    unlink(created)
    stop("Registration changed while plotting; keep registration.json frozen")
  }
}
protocol <- fromJSON(protocol_path)
source_text <- readLines(input_path, warn = FALSE)
raw <- fromJSON(paste(source_text, collapse = "\n"))
required <- c("scenario", "variant", "nprobe", "repetition", "queries", "recall", "qps")
variants <- c("graph", "min1", "min3", "min5", "min10")
if (member_postfilter) {
  variants <- c("graph", "row_min1", "all_members_min1", "all_members_min10")
} else if (postgraph_comparison) {
  variants <- c("graph", "postgraph_shared", "postgraph_extra", "graph_total")
} else if (compact_storage) {
  variants <- c("original_layout", "compact_layout")
}
baseline <- variants[[1]]
scenario_order <- c("unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf")
titles <- c(unfilter = "Unfiltered", broad_tag = "Broad categorical",
            medium_tag = "Medium categorical", extreme_tag = "Extreme categorical",
            numeric = "Numeric", mixed_dnf = "Mixed DNF")
colors <- c(graph = "#0072B2", min1 = "#009E73", min3 = "#56B4E9",
            min5 = "#CC79A7", min10 = "#D55E00")
shapes <- c(graph = 1, min1 = 17, min3 = 15, min5 = 18, min10 = 2)
styles <- c(graph = 1, min1 = 2, min3 = 3, min5 = 4, min10 = 5)
labels <- c(graph = "H1-only", min1 = "min = 1", min3 = "min = 3",
            min5 = "min = 5", min10 = "min = 10")
if (member_postfilter) {
  colors <- c(graph = "#0072B2", row_min1 = "#888888",
              all_members_min1 = "#009E73", all_members_min10 = "#D55E00")
  shapes <- c(graph = 1, row_min1 = 15, all_members_min1 = 17, all_members_min10 = 2)
  styles <- c(graph = 1, row_min1 = 2, all_members_min1 = 1, all_members_min10 = 3)
  labels <- c(graph = "H1-only", row_min1 = "Old member filter, min = 1",
              all_members_min1 = "All members, min = 1",
              all_members_min10 = "All members, min = 10")
}
budgets <- NULL
search_settings <- NULL
integer_value <- function(x, minimum, maximum = .Machine$integer.max) {
  is.numeric(x) && length(x) == 1L && is.finite(x) &&
    x == floor(x) && x >= minimum && x <= maximum
}
grid <- protocol$nprobe
repetitions <- protocol$repetitions
query_count <- protocol$query_count
registered_scenarios <- protocol$scenarios
scenario_metadata <- NULL
if (selectivity) {
  declaration <- fromJSON(protocol_path, simplifyVector = FALSE)
  nonempty_string <- function(x) {
    is.character(x) && length(x) == 1L && !is.na(x) && nzchar(trimws(x))
  }
  json_array <- function(x, scalar) {
    is.list(x) && is.null(names(x)) && length(x) > 0L &&
      all(vapply(x, scalar, logical(1)))
  }
  if (anyDuplicated(names(declaration)) ||
      !json_array(declaration$scenarios, nonempty_string) ||
      !json_array(declaration$variants, nonempty_string) ||
      !json_array(declaration$nprobe, function(x) integer_value(x, 10)) ||
      !integer_value(declaration$query_count, 1) || declaration$query_count != 1000 ||
      !integer_value(declaration$repetitions, 2) || declaration$repetitions != 2) {
    stop("Unexpected selectivity registration: require scenario/method arrays and two 1000-query runs")
  }
}
valid_repetitions <- integer_value(repetitions, 2) &&
  if (compact_storage) repetitions %% 2 == 0 else repetitions == 2L
if (!setequal(protocol$variants, variants) ||
    anyDuplicated(protocol$variants) ||
    !is.character(registered_scenarios) || length(registered_scenarios) == 0L ||
    anyNA(registered_scenarios) || anyDuplicated(registered_scenarios) ||
    (!selectivity && any(!registered_scenarios %in% scenario_order)) ||
    (!member_postfilter && !selectivity && !setequal(registered_scenarios, scenario_order)) ||
    (member_postfilter && !"medium_tag" %in% registered_scenarios) ||
    !is.numeric(grid) || length(grid) < 2L || anyDuplicated(grid) ||
    any(!is.finite(grid) | grid < 10 | grid != floor(grid)) ||
    !valid_repetitions ||
    length(query_count) != 1L || query_count != 1000L) {
  stop(paste("Unexpected protocol: require the selected methods, valid registered scenarios and",
             if (compact_storage) "an even count of at least two 1000-query runs"
             else "two 1000-query runs"))
}
if (selectivity) {
  if (!identical(as.double(grid), c(16, 24, 48, 96, 192, 384))) {
    stop("Unexpected selectivity nprobe grid: require 16, 24, 48, 96, 192, 384 in order")
  }
  scenario_order <- registered_scenarios
  scenario_metadata <- declaration$scenario_metadata
  if (!is.list(scenario_metadata) ||
      !setequal(names(scenario_metadata), registered_scenarios) ||
      anyDuplicated(names(scenario_metadata))) {
    stop("Invalid scenario_metadata: require exactly one object per registered scenario")
  }
  for (scenario in scenario_order) {
    metadata <- scenario_metadata[[scenario]]
    if (!is.list(metadata) || anyDuplicated(names(metadata)) ||
        !all(c("title", "selectivity") %in% names(metadata)) ||
        !nonempty_string(metadata[["title"]]) ||
        !is.numeric(metadata[["selectivity"]]) || length(metadata[["selectivity"]]) != 1L ||
        !is.finite(metadata[["selectivity"]]) ||
        metadata[["selectivity"]] < 0 || metadata[["selectivity"]] > 1) {
      stop(paste("Invalid scenario_metadata title or scalar selectivity in [0,1]:", scenario))
    }
    if (any(c("eligible_count", "corpus_count") %in% names(metadata))) {
      eligible <- metadata[["eligible_count"]]
      corpus <- metadata[["corpus_count"]]
      if (!integer_value(eligible, 0, 2^53 - 1) || !integer_value(corpus, 1, 2^53 - 1) ||
          eligible > corpus || abs(metadata[["selectivity"]] - eligible / corpus) > 1e-12) {
        stop(paste("Invalid scenario_metadata counts: require eligible/corpus to match selectivity:",
                   scenario))
      }
    }
    if (scenario == "unfilter" && metadata[["selectivity"]] != 1) {
      stop("Invalid scenario_metadata: unfilter must have selectivity 1 (100%, no filter)")
    }
  }
  titles <- vapply(scenario_metadata[scenario_order], function(x) x[["title"]], character(1))
  selectivity_labels <- vapply(scenario_order, function(scenario) {
    paste0("Actual selectivity: ",
           format(100 * scenario_metadata[[scenario]][["selectivity"]],
                  digits = 10L, scientific = FALSE, trim = TRUE),
           "%", if (scenario == "unfilter") " (no filter)" else "")
  }, character(1))
}
if (postgraph_comparison) {
  budgets <- if (selectivity) declaration$budgets else protocol$budgets
  if (!is.list(budgets) || !setequal(names(budgets), variants) ||
      anyDuplicated(names(budgets)) ||
      !integer_value(protocol$posting_anchor_count, 1) ||
      (selectivity && !integer_value(declaration$posting_anchor_count, 1))) {
    stop("Unexpected postgraph budget declaration")
  }
  for (variant in variants) {
    budget <- budgets[[variant]]
    if (!is.list(budget) || !integer_value(budget$graph_maxcheck, 1) ||
        !integer_value(budget$posting_additional_maxcheck, 0) ||
        (selectivity && (anyDuplicated(names(budget)) ||
                        !all(c("graph_maxcheck", "posting_additional_maxcheck") %in% names(budget))))) {
      stop("Unexpected postgraph budget declaration")
    }
  }
  graph_budget <- budgets$graph$graph_maxcheck
  extra_budget <- budgets$postgraph_extra$posting_additional_maxcheck
  if (budgets$graph$posting_additional_maxcheck != 0 ||
      budgets$graph_total$posting_additional_maxcheck != 0 ||
      budgets$postgraph_shared$posting_additional_maxcheck != 0 ||
      budgets$postgraph_shared$graph_maxcheck != graph_budget ||
      budgets$postgraph_extra$graph_maxcheck != graph_budget ||
      extra_budget <= 0 ||
      budgets$graph_total$graph_maxcheck != graph_budget + extra_budget) {
    stop("Unexpected postgraph budget declaration")
  }
  colors <- c(graph = "#0072B2", postgraph_shared = "#009E73",
              postgraph_extra = "#D55E00", graph_total = "#666666")
  shapes <- c(graph = 1, postgraph_shared = 17, postgraph_extra = 18, graph_total = 15)
  styles <- c(graph = 1, postgraph_shared = 2, postgraph_extra = 1, graph_total = 3)
  labels <- c(graph = paste("H1", graph_budget),
              postgraph_shared = paste0("Postgraph ", graph_budget, "+0"),
              postgraph_extra = paste0("Postgraph ", graph_budget, "+", extra_budget),
              graph_total = paste("H1", budgets$graph_total$graph_maxcheck))
} else if (compact_storage) {
  search_settings <- protocol$search_settings
  if (!is.list(search_settings) ||
      !setequal(names(search_settings),
                c("graph_maxcheck", "posting_additional_maxcheck", "posting_anchor_count")) ||
      anyDuplicated(names(search_settings)) || !is.null(protocol$budgets) ||
      !integer_value(search_settings$graph_maxcheck, 1) ||
      !integer_value(search_settings$posting_additional_maxcheck, 0) ||
      !integer_value(search_settings$posting_anchor_count, 1) ||
      as.double(search_settings$graph_maxcheck) +
        search_settings$posting_additional_maxcheck > .Machine$integer.max) {
    stop("Unexpected compact-storage search settings; require one common declaration")
  }
  colors <- c(original_layout = "#0072B2", compact_layout = "#D55E00")
  shapes <- c(original_layout = 1, compact_layout = 17)
  styles <- c(original_layout = 1, compact_layout = 2)
  labels <- c(original_layout = "Original layout", compact_layout = "Compact layout")
}
if (!is.data.frame(raw) || !all(required %in% names(raw)) || nrow(raw) == 0L) {
  stop("No usable ordinary measurement records")
}
if (selectivity &&
    (!is.character(raw$scenario) || !is.character(raw$variant) ||
     !all(vapply(raw[setdiff(required, c("scenario", "variant"))], is.numeric, logical(1))) ||
     ("diagnostic" %in% names(raw) && (!is.logical(raw$diagnostic) || anyNA(raw$diagnostic))))) {
  stop("Invalid or mixed measurement records; require scalar ordinary measurements")
}
if (compact_storage) {
  record_settings <- c(graph_maxcheck = "graph_maxcheck", max_check = "graph_maxcheck",
                       posting_additional_maxcheck = "posting_additional_maxcheck",
                       posting_additional_max_check = "posting_additional_maxcheck",
                       posting_anchor_count = "posting_anchor_count")
  for (setting in intersect(names(record_settings), names(raw))) {
    values <- raw[[setting]]
    if (!is.numeric(values) || anyNA(values) ||
        any(!is.finite(values) | values != search_settings[[record_settings[[setting]]]])) {
      stop("Compact-storage records mix search settings")
    }
  }
}
rows <- raw[required]
if (anyNA(rows) || any(!is.finite(as.matrix(rows[c("recall", "qps")]))) ||
    any(rows$recall < 0 | rows$recall > 1 | rows$qps <= 0) ||
    any(rows$queries != query_count) || any(!rows$nprobe %in% grid) ||
    any(!rows$scenario %in% registered_scenarios) || any(!rows$variant %in% variants) ||
    any(!rows$repetition %in% seq_len(repetitions)) ||
    ("diagnostic" %in% names(raw) && any(raw$diagnostic))) {
  stop("Invalid or mixed measurement records; diagnostic timings must not be plotted")
}
key <- function(x) paste(x$scenario, x$variant, x$nprobe, x$repetition, sep = "/")
if (anyDuplicated(key(rows))) stop("Duplicate measurement points")

complete <- list()
retained <- list()
for (scenario in scenario_order) {
  if (!scenario %in% registered_scenarios) next
  available <- integer()
  for (repetition in seq_len(repetitions)) {
    block <- rows[rows$scenario == scenario & rows$repetition == repetition, ]
    expected <- expand.grid(scenario = scenario, variant = variants,
                            nprobe = grid, repetition = repetition)
    if (identical(sort(key(block)), sort(key(expected)))) available <- c(available, repetition)
  }
  if (!partial && length(available) != repetitions) {
    stop(paste("Incomplete registered-method curve matrix:", scenario))
  }
  if (length(available) > 0L) {
    complete[[scenario]] <- available
    retained[[scenario]] <- rows[rows$scenario == scenario &
                                  rows$repetition %in% available, ]
  }
}
if (length(retained) == 0L) stop("No scenario has a complete registered-method nprobe sweep yet")
used <- do.call(rbind, retained)
groups <- split(used, paste(used$scenario, used$variant, used$nprobe, sep = "/"))
points <- do.call(rbind, lapply(groups, function(x) {
  if (length(unique(x$recall)) != 1L) stop("Recall differs between deterministic repetitions")
  data.frame(scenario = x$scenario[1], variant = x$variant[1], nprobe = x$nprobe[1],
             recall = x$recall[1], qps = mean(x$qps),
             qps_min = min(x$qps), qps_max = max(x$qps), runs = nrow(x))
}))
rownames(points) <- NULL
points <- points[order(match(points$scenario, scenario_order),
                       match(points$variant, variants), points$nprobe), ]
scenarios <- scenario_order
files <- c("recall_qps.png", "recall_qps.pdf", "plotted_points.csv",
           "nprobe24.csv", "source_points.json", "plot_metadata.json")
check_registration()
if (any(file.exists(file.path(output, files)))) stop("Refusing to overwrite existing figures")
if (!dir.exists(output) && !dir.create(output, recursive = TRUE)) stop("Cannot create output directory")

panels <- length(scenarios)
columns <- if (selectivity) min(3L, panels) else 3L
panel_rows <- if (selectivity) ceiling(panels / columns) else 2L
width <- 16
height <- if (selectivity) 2.2 + 4.4 * panel_rows else 11
ymax <- ceiling(max(points$qps_max) / 500) * 500
panel_title <- function(scenario, suffix) {
  title <- paste0(titles[[scenario]], suffix)
  if (selectivity) paste(title, selectivity_labels[[scenario]], sep = "\n") else title
}
draw <- function() {
  par(family = "sans", fg = "#253247", col.axis = "#445166",
      col.lab = "#253247", col.main = "#17243A",
      mfrow = c(panel_rows, columns), mar = c(4.2, 4.6, if (selectivity) 3.6 else 2.8, 1.1),
      omi = c(0.8, 0.08, 1.35, 0.08), las = 1, tcl = -0.25)
  for (scenario in scenarios) {
    if (!scenario %in% names(complete)) {
      registered <- scenario %in% registered_scenarios
      plot(NA, xlim = c(0, 1), ylim = c(0, ymax), xaxs = "i", yaxs = "i",
           xlab = "Recall@10", ylab = "Queries / second",
           main = panel_title(scenario,
                              if (registered) "  (pending)" else "  (not measured)"), axes = FALSE)
      box(col = "#BBC5D1")
      if (!registered) {
        text(0.5, ymax * 0.53, "Not measured in this campaign",
             cex = 1, col = "#66758A")
        text(0.5, ymax * 0.35, "No historical timings substituted",
             cex = 0.85, col = "#66758A")
        next
      }
      counts <- vapply(seq_len(repetitions), function(repetition) {
        sum(rows$scenario == scenario & rows$repetition == repetition)
      }, integer(1))
      text(0.5, ymax * 0.58,
           paste0("Awaiting complete\n", length(variants), "-method nprobe sweep"),
           cex = 1, col = "#66758A")
      text(0.5, ymax * 0.32,
           paste0("Run ", seq_len(repetitions), ": ", counts, "/",
                  length(variants) * length(grid), " points", collapse = "\n"),
           cex = 0.85, col = "#66758A")
      next
    }
    block <- points[points$scenario == scenario, ]
    xrange <- range(block$recall)
    padding <- max(0.008, diff(xrange) * 0.05)
    xlim <- c(max(0, xrange[1] - padding), min(1, xrange[2] + padding))
    run_count <- length(complete[[scenario]])
    title <- panel_title(scenario, paste0("  (", run_count,
                                         if (run_count == 1L) " run)" else " runs)"))
    plot(NA, xlim = xlim, ylim = c(0, ymax), xaxs = "i", yaxs = "i",
         xlab = "Recall@10", ylab = "Queries / second", main = title, axes = FALSE)
    abline(h = pretty(c(0, ymax)), col = "#E5EAF0", lwd = 0.8)
    axis(1, cex.axis = 0.94)
    axis(2, at = pretty(c(0, ymax)), cex.axis = 0.94)
    box(col = "#BBC5D1")
    for (variant in rev(variants)) {
      x <- block[block$variant == variant, ]
      x <- x[order(x$nprobe), ]
      segments(x$recall, x$qps_min, x$recall, x$qps_max,
               col = adjustcolor(colors[[variant]], alpha.f = 0.4), lwd = 1.3)
      lines(x$recall, x$qps, col = colors[[variant]], lty = styles[[variant]], lwd = 2)
      points(x$recall, x$qps, col = colors[[variant]], pch = shapes[[variant]],
             cex = if (variant == baseline) 1.1 else 0.9, lwd = 1.5)
    }
    baseline_range <- range(block$recall[block$variant == baseline])
    disjoint <- vapply(setdiff(variants, baseline), function(variant) {
      r <- range(block$recall[block$variant == variant])
      max(r[1], baseline_range[1]) > min(r[2], baseline_range[2])
    }, logical(1))
    if (all(disjoint)) {
      text(mean(xlim), ymax * 0.045,
           if (compact_storage) "No measured recall overlap with original layout"
           else "No measured recall overlap with H1-only",
           cex = 0.8, col = "#80512B")
    }
  }
  if (panels < panel_rows * columns) {
    for (i in seq_len(panel_rows * columns - panels)) plot.new()
  }
  mtext("Lines connect measured nprobe points only; recall-axis ranges vary by panel.",
        outer = TRUE, side = 1, line = 1, cex = 0.88)
  mtext("QPS: mean of available complete runs. Vertical bars: run range, not confidence intervals.",
        outer = TRUE, side = 1, line = 2.3, cex = 0.88)
  mtext(if (postgraph_comparison)
          "MaxCheck = graph + optional extra; complete posting rows may exceed the remaining budget."
        else if (compact_storage)
          "Layout-only comparison; graph MaxCheck, posting extra budget and anchor count are identical."
        else if (member_postfilter)
          "New policy scores nonmatches too; minimum counts fresh matching H1, not all scored H1."
        else "Minimum counts fresh matching H1 candidates per activation; complete posting rows may overshoot.",
        outer = TRUE, side = 1, line = 3.6, cex = 0.88)
  par(fig = c(0, 1, 0, 1), mar = c(0, 0, 0, 0), oma = c(0, 0, 0, 0),
      cex = 1, new = TRUE)
  plot.new()
  par(usr = c(0, 1, 0, 1))
  comparison_title <- if (selectivity) "Post-graph posting supplementation by selectivity"
    else if (postgraph) "Post-graph posting supplementation"
    else if (compact_storage) "Compact storage"
    else if (member_postfilter) "Posting member post-filter" else "Posting minimum sweep"
  text(0.5, 1 - 0.30 / height,
       paste0(comparison_title, if (partial) ": preliminary measured curves"
              else ": Recall-QPS comparison"),
       cex = 1.55, font = 2)
  text(0.5, 1 - 0.66 / height,
       paste0(format(query_count, big.mark = ","), " queries | ",
              if (postgraph_comparison) paste0("Same runtime/index | Anchors: ", protocol$posting_anchor_count)
              else if (compact_storage)
                paste0("Same policy/cohort | MaxCheck: ", search_settings$graph_maxcheck,
                       "+", search_settings$posting_additional_maxcheck,
                       " | Anchors: ", search_settings$posting_anchor_count)
              else if (member_postfilter) "Same index/cohort; frozen runtime per policy"
              else "Same frozen runtime and index",
              " | nprobe: ",
              paste(grid, collapse = ", ")),
       cex = 0.94)
  legend("top", inset = c(0, 0.91 / height), legend = labels[variants], col = colors[variants],
         pch = shapes[variants], lty = styles[variants], lwd = 2, horiz = TRUE,
         bty = "n", cex = 1.02)
}
for (format in c("png", "pdf")) {
  path <- file.path(output, paste0("recall_qps.", format))
  if (format == "png") {
    png(path, width = width, height = height, units = "in", res = 180, type = "cairo", bg = "white")
  } else {
    cairo_pdf(path, width = width, height = height, bg = "white")
  }
  tryCatch(draw(), finally = dev.off())
}
write.csv(points, file.path(output, "plotted_points.csv"), row.names = FALSE)
write.csv(points[points$nprobe == 24, ], file.path(output, "nprobe24.csv"), row.names = FALSE)
writeLines(source_text, file.path(output, "source_points.json"), useBytes = TRUE)
check_registration(file.path(output, setdiff(files, "plot_metadata.json")))
plot_metadata <- list(
  comparison = if (selectivity) "posting_selectivity"
    else if (postgraph) "posting_postgraph"
    else if (compact_storage) "compact_storage"
    else if (member_postfilter) "posting_member_postfilter" else "posting_minimum_sweep",
  partial = partial, scenarios = scenarios, complete_repetitions = complete,
  registered_scenarios = registered_scenarios,
  completed_scenarios = names(complete),
  pending_scenarios = setdiff(registered_scenarios, names(complete)),
  unmeasured_scenarios = setdiff(scenarios, registered_scenarios),
  query_count = query_count, nprobe = grid, variants = variants,
  registered_repetitions = repetitions,
  budgets = budgets,
  search_settings = search_settings,
  posting_anchor_count = if (postgraph_comparison) protocol$posting_anchor_count
    else if (compact_storage) search_settings$posting_anchor_count else NULL,
  raw_points_used = nrow(used), plotted_means = nrow(points),
  registration_md5 = if (selectivity) registration_md5
    else as.character(tools::md5sum(protocol_path)),
  data_snapshot_md5 = as.character(tools::md5sum(file.path(output, "source_points.json"))),
  diagnostic_timings_used = FALSE, interpolated = FALSE,
  qps_aggregation = "Arithmetic mean of complete repetitions within each scenario",
  uncertainty = "Observed run range; no equivalence proof or 1B prediction",
  r_version = R.version.string
)
if (selectivity) plot_metadata$scenario_metadata <- scenario_metadata
write_json(plot_metadata, file.path(output, "plot_metadata.json"), pretty = TRUE, auto_unbox = TRUE,
           digits = if (selectivity) NA else 4)
cat("Created", panels, "scenario panels;", length(complete), "with complete curves;",
    nrow(points), "measured curve points in", output, "\n")

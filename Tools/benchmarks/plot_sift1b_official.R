#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if ("--selectivity" %in% args) {
  if (length(args) != 3L || args[[3]] != "--selectivity") {
    stop("usage: plot_sift1b_official.R <run-directory> <new-output-directory> --selectivity")
  }
  suppressPackageStartupMessages(library(ggplot2))
  suppressPackageStartupMessages(library(jsonlite))
  render_selectivity <- function(root, output) {
    root <- normalizePath(root, mustWork = TRUE)
    inputs <- c(summary = normalizePath(file.path(root, "summary.csv"), mustWork = TRUE),
                registration = normalizePath(file.path(root, "plot_registration.json"), mustWork = TRUE))
    hash_files <- function(paths) {
      hashes <- unname(tools::md5sum(paths))
      if (anyNA(hashes)) stop("Cannot hash plotting input or output files")
      hashes
    }
    input_hashes <- setNames(hash_files(inputs), names(inputs))
    check_inputs <- function() {
      if (!identical(hash_files(inputs), unname(input_hashes))) {
        stop("Frozen plotting inputs changed during rendering")
      }
    }
    object <- function(x) {
      is.list(x) && length(x) > 0L && !is.null(names(x)) &&
        !anyDuplicated(names(x)) && all(nzchar(trimws(names(x))))
    }
    text_value <- function(x) {
      is.character(x) && length(x) == 1L && !is.na(x) && nzchar(trimws(x))
    }
    number <- function(x) is.numeric(x) && length(x) == 1L && is.finite(x)
    integer_value <- function(x, minimum = 0, maximum = .Machine$integer.max) {
      number(x) && x == floor(x) && x >= minimum && x <= maximum
    }
    array <- function(x, scalar) {
      is.list(x) && is.null(names(x)) && length(x) > 0L && all(vapply(x, scalar, logical(1)))
    }
    scalar <- function(x) {
      text_value(x) || number(x) || (is.logical(x) && length(x) == 1L && !is.na(x))
    }
    declaration <- fromJSON(inputs[["registration"]], simplifyVector = FALSE)
    scenarios <- c("unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf")
    if (!object(declaration) || !integer_value(declaration[["schema_version"]], 1) ||
        declaration$schema_version != 1 ||
        !identical(declaration[["dataset"]], "SIFT1B") ||
        !integer_value(declaration[["corpus_count"]], 1) || declaration$corpus_count != 1000000000 ||
        !text_value(declaration[["comparison"]]) ||
        !declaration$comparison %in% c("fresh_paired", "historical", "reused_pipeann_baseline") ||
        !array(declaration[["scenarios"]], text_value) ||
        !identical(unlist(declaration$scenarios, use.names = FALSE), scenarios)) {
      stop("Invalid selectivity registration: require SIFT1B and the five ordered scenarios")
    }
    historical <- declaration$comparison == "historical"
    reused_pipeann <- declaration$comparison == "reused_pipeann_baseline"
    if (!is.null(declaration[["caption_note"]]) && !text_value(declaration$caption_note)) {
      stop("Invalid caption_note")
    }
    scenario_metadata <- declaration[["scenario_metadata"]]
    if (!object(scenario_metadata) || !setequal(names(scenario_metadata), scenarios)) {
      stop("Invalid scenario_metadata: require exactly the five registered scenarios")
    }
    for (scenario in scenarios) {
      entry <- scenario_metadata[[scenario]]
      if (!object(entry) || !text_value(entry[["title"]]) || !text_value(entry[["predicate"]]) ||
          !integer_value(entry[["candidate_count"]]) || entry$candidate_count > declaration$corpus_count ||
          !number(entry[["selectivity"]]) || entry$selectivity < 0 || entry$selectivity > 1 ||
          abs(entry$selectivity - entry$candidate_count / declaration$corpus_count) > 1e-12 ||
          (scenario == "unfilter" && entry$selectivity != 1)) {
        stop(paste("Invalid scenario_metadata predicate/count/actual selectivity:", scenario))
      }
    }
    engine_order <- c("SPTAG_adaptive", "SPTAG_H1", "PipeANN")
    engines <- declaration[["engines"]]
    if (!object(engines) || !all(c("SPTAG_adaptive", "PipeANN") %in% names(engines)) ||
        any(!names(engines) %in% engine_order)) {
      stop("Register SPTAG_adaptive and PipeANN, optionally SPTAG_H1; no other engines")
    }
    engine_order <- engine_order[engine_order %in% names(engines)]
    series <- declaration[["series"]]
    if (!is.null(series) &&
        (!text_value(series) || !series %in% c("adaptive_only", "with_h1_control") ||
         !identical(engine_order, if (series == "adaptive_only")
           c("SPTAG_adaptive", "PipeANN") else c("SPTAG_adaptive", "SPTAG_H1", "PipeANN")))) {
      stop("Declared series must match the complete engine set")
    }
    for (id in engine_order) {
      engine <- engines[[id]]
      spann <- id != "PipeANN"
      if (!object(engine) ||
          !identical(engine[["native_control"]], if (spann) "nprobe" else "searchL") ||
          !array(engine[["L"]], function(x) integer_value(x, 10)) || length(engine$L) < 2L ||
          is.unsorted(unlist(engine$L), strictly = TRUE) ||
          !integer_value(engine[["repeats"]], 1) || !integer_value(engine[["queries"]], 1) ||
          !integer_value(engine[["threads"]], 1) ||
          !all(vapply(engine[c("query_cohort_id", "cpu_nodes", "memory_nodes", "index_id",
                              "runtime_id", "search_policy")], text_value, logical(1))) ||
          !text_value(engine[["qps_aggregation"]]) ||
          !engine$qps_aggregation %in% c("arithmetic_mean", "median") ||
          !identical(engine[["io_mode"]], if (spann) "buffered" else "direct") ||
          !object(engine[["controls"]]) || !all(vapply(engine$controls, scalar, logical(1)))) {
        stop(paste("Invalid engine declaration, native control, grid, provenance or I/O:", id))
      }
      controls <- engine$controls
      if (spann &&
          (!integer_value(controls[["graph_maxcheck"]], 1) ||
           !integer_value(controls[["posting_additional_maxcheck"]]) ||
           !integer_value(controls[["posting_anchor_count"]], 1) ||
           as.double(controls$graph_maxcheck) + controls$posting_additional_maxcheck > .Machine$integer.max ||
           !identical(controls[["enable_posting_navigation"]], id == "SPTAG_adaptive") ||
           !identical(engine$search_policy, if (id == "SPTAG_adaptive")
             "predicate_first_adaptive_global_posting_frontier" else "h1_only"))) {
        stop(paste("Invalid SPTAG controls or frozen search policy:", id))
      }
      source_date <- engine[["source_date"]]
      if ((historical || reused_pipeann || !is.null(source_date)) &&
          (!text_value(source_date) || !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", source_date) ||
           is.na(as.Date(source_date, format = "%Y-%m-%d")))) {
        stop(paste("Historical sources require explicit valid source_date and NUMA placement:", id))
      }
      if (reused_pipeann && !identical(engine[["measurement_reused"]], id == "PipeANN")) {
        stop("Reused PipeANN comparison requires explicit per-engine measurement_reused flags")
      }
    }
    if (!historical) {
      for (field in c("queries", "query_cohort_id", "threads", "cpu_nodes", "memory_nodes")) {
        if (length(unique(vapply(engines, function(x) as.character(x[[field]]), character(1)))) != 1L) {
          stop(paste("Fresh paired comparison requires matching", field))
        }
      }
      if ("SPTAG_H1" %in% engine_order) {
        for (field in c("index_id", "runtime_id")) {
          if (engines$SPTAG_adaptive[[field]] != engines$SPTAG_H1[[field]]) {
            stop(paste("Fresh SPTAG H1 control requires the same", field))
          }
        }
      }
    }
    required <- c("scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
                  "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
                  "candidate_count", "selectivity", "predicate")
    raw <- read.csv(inputs[["summary"]], colClasses = "character", check.names = FALSE,
                    na.strings = character(), stringsAsFactors = FALSE)
    if (!all(required %in% names(raw)) || anyDuplicated(names(raw)) || nrow(raw) == 0L ||
        anyNA(raw[required]) || any(!vapply(raw[required], function(x) all(nzchar(trimws(x))), logical(1)))) {
      stop("Invalid summary.csv schema or missing measurement fields")
    }
    points <- raw
    numeric_fields <- c(setdiff(required, c("scenario", "engine", "cpu_nodes", "predicate")),
                        intersect("corpus_count", names(raw)))
    for (field in numeric_fields) {
      points[[field]] <- suppressWarnings(as.numeric(raw[[field]]))
      if (any(!is.finite(points[[field]]))) stop(paste("Invalid numeric measurement field:", field))
    }
    for (field in c("L", "queries", "repeats", "threads", "candidate_count")) {
      minimum <- if (field == "candidate_count") 0 else 1
      if (any(points[[field]] != floor(points[[field]]) |
              points[[field]] < minimum |
              points[[field]] > .Machine$integer.max)) {
        stop(paste("Invalid integer measurement field:", field))
      }
    }
    if (any(points$recall_min < 0 | points$recall_max > 1 |
            points$recall_min > points$recall | points$recall > points$recall_max |
            points$qps_min <= 0 | points$qps_min > points$qps | points$qps > points$qps_max |
            points$selectivity < 0 | points$selectivity > 1) ||
        ("diagnostic" %in% names(raw) && any(tolower(raw$diagnostic) != "false")) ||
        ("stage" %in% names(raw) && any(raw$stage != "measured"))) {
      stop("Invalid performance ranges or non-ordinary measurement records")
    }
    if (any(!points$scenario %in% scenarios) || any(!points$engine %in% engine_order)) {
      stop("Unregistered scenario or engine in summary.csv")
    }
    if (anyDuplicated(points[c("scenario", "engine", "L")])) stop("Duplicate measured control points")
    for (scenario in scenarios) {
      entry <- scenario_metadata[[scenario]]
      panel <- points[points$scenario == scenario, ]
      if (any(panel$predicate != entry$predicate | panel$candidate_count != entry$candidate_count |
              abs(panel$selectivity - entry$selectivity) > 1e-12)) {
        stop(paste("Inconsistent panel predicate/count/selectivity:", scenario))
      }
      for (id in engine_order) {
        engine <- engines[[id]]
        block <- panel[panel$engine == id, ]
        grid <- unlist(engine$L, use.names = FALSE)
        if (nrow(block) != length(grid) || !setequal(block$L, grid)) {
          stop(paste("Incomplete registered native-control grid:", scenario, id))
        }
        if (any(block$repeats != engine$repeats)) {
          stop(paste("Incomplete or inconsistent repetitions:", scenario, id))
        }
        for (field in c("queries", "threads", "cpu_nodes", "query_cohort_id", "memory_nodes",
                        "io_mode", "index_id", "runtime_id", "search_policy", "qps_aggregation",
                        "native_control", "source_date")) {
          if (field %in% names(block) &&
              (is.null(engine[[field]]) || any(block[[field]] != engine[[field]]))) {
            stop(paste("Measurement disagrees with registered", field, ":", scenario, id))
          }
        }
        if ("corpus_count" %in% names(block) &&
            any(block$corpus_count != declaration$corpus_count)) {
          stop("Measurement disagrees with registered corpus_count")
        }
        if (engine$repeats == 1 &&
            any(block$qps_min != block$qps | block$qps_max != block$qps |
                block$recall_min != block$recall | block$recall_max != block$recall)) {
          stop(paste("Single-repetition ranges must equal the measured point:", scenario, id))
        }
      }
    }
    ordering <- order(match(points$scenario, scenarios), match(points$engine, engine_order), points$L)
    points <- points[ordering, ]
    raw <- raw[ordering, ]
    points$scenario <- factor(points$scenario, levels = scenarios)
    points$engine <- factor(points$engine, levels = engine_order)
    engine_labels <- c(SPTAG_adaptive = "SPTAG adaptive", SPTAG_H1 = "SPTAG H1-only", PipeANN = "PipeANN")
    if ("SPTAG_H1" %in% engine_order) {
      engine_labels[["SPTAG_H1"]] <- paste0("SPTAG H1-only (MaxCheck ",
                                           engines$SPTAG_H1$controls$graph_maxcheck, ")")
    }
    colors <- c(SPTAG_adaptive = "#009E73", SPTAG_H1 = "#0072B2", PipeANN = "#D55E00")
    shapes <- c(SPTAG_adaptive = 17, SPTAG_H1 = 1, PipeANN = 18)
    line_types <- c(SPTAG_adaptive = "solid", SPTAG_H1 = "dashed", PipeANN = "dotdash")
    density_labels <- vapply(scenarios, function(scenario) {
      entry <- scenario_metadata[[scenario]]
      paste0("Actual selectivity: ",
             format(100 * entry$selectivity, digits = 10L, scientific = FALSE, trim = TRUE), "%",
             if (scenario == "unfilter") " (no filter)" else "", "; ",
             format(entry$candidate_count, big.mark = ",", scientific = FALSE, trim = TRUE), " eligible")
    }, character(1))
    panel_labels <- vapply(scenarios, function(scenario) {
      paste(scenario_metadata[[scenario]]$title, sub("; ", "\n", density_labels[[scenario]]), sep = "\n")
    }, character(1))
    comparison_label <- if (historical) "Historical comparison - not a fresh paired run"
      else if (reused_pipeann) "SPTAG rerun with preserved PipeANN baseline; matched query cohort and CPU/memory NUMA placement"
      else "Fresh paired query cohort and CPU/memory NUMA placement"
    detail_lines <- vapply(engine_order, function(id) {
      engine <- engines[[id]]
      paste0(engine_labels[[id]], ": ",
             if (engine$qps_aggregation == "arithmetic_mean") "arithmetic-mean" else "median",
             " QPS; ", engine$repeats, " complete runs; ", engine$queries, " queries; ",
             engine$threads, " query thread(s); CPU NUMA ", engine$cpu_nodes,
             " / memory NUMA ", engine$memory_nodes, "; ", engine$io_mode, " I/O",
             if (!is.null(engine$source_date)) paste0("; source ", engine$source_date) else "", ".\n",
             "  Native ", engine$native_control, " = [", paste(unlist(engine$L), collapse = ", "), "]; ",
             paste(paste0(names(engine$controls), "=", vapply(engine$controls, as.character, character(1))),
                   collapse = ", "), ".")
    }, character(1))
    h1_baseline_note <- character()
    if ("SPTAG_H1" %in% engine_order) {
      same_index_runtime <- all(vapply(c("index_id", "runtime_id"), function(field) {
        identical(engines$SPTAG_adaptive[[field]], engines$SPTAG_H1[[field]])
      }, logical(1)))
      h1_baseline_note <- paste0("H1-only is ",
                                if (same_index_runtime) "the same-index/runtime project baseline"
                                else "this project's baseline",
                                " with posting disabled, not an unmodified Microsoft SPTAG build.")
    }
    caption <- function(width) {
      lines <- c(comparison_label, detail_lines,
                 "SPTAG adaptive uses the frozen predicate-first adaptive global-posting frontier.",
                 h1_baseline_note,
                 "I/O differs: SPTAG buffered versus PipeANN direct. Not a matched-I/O algorithm-only comparison.",
                 "L is engine-specific (SPTAG nprobe; PipeANN searchL), not equal work. Lines follow native-control order without smoothing or extrapolation.",
                 "All measured points are retained. Horizontal/vertical spans are observed recall/QPS repetition ranges, not confidence intervals.")
      paste(unlist(lapply(strsplit(paste(lines, collapse = "\n"), "\n")[[1]],
                          function(line) strwrap(line, width = width))), collapse = "\n")
    }
    make_plot <- function(frame, caption_text) {
      ggplot(frame, aes(recall, qps, colour = engine, shape = engine,
                        linetype = engine, group = engine)) +
        geom_linerange(aes(ymin = qps_min, ymax = qps_max), linewidth = 0.35,
                       alpha = 0.4, show.legend = FALSE) +
        geom_segment(aes(x = recall_min, xend = recall_max, yend = qps), linewidth = 0.35,
                     alpha = 0.4, show.legend = FALSE) +
        geom_path(linewidth = 0.75) + geom_point(size = 2.5, stroke = 0.8) +
        scale_colour_manual(values = colors, limits = engine_order, labels = engine_labels[engine_order]) +
        scale_shape_manual(values = shapes, limits = engine_order, labels = engine_labels[engine_order]) +
        scale_linetype_manual(values = line_types, limits = engine_order, labels = engine_labels[engine_order]) +
        scale_x_continuous(limits = c(0, 1), breaks = seq(0, 1, 0.2), expand = expansion(mult = 0)) +
        scale_y_log10(limits = range(points$qps_min, points$qps_max),
                      labels = function(x) format(x, scientific = FALSE, big.mark = ",", trim = TRUE)) +
        coord_cartesian(clip = "off") +
        labs(x = "Recall@10", y = "Queries per second (log scale)",
             colour = NULL, shape = NULL, linetype = NULL, caption = caption_text) +
        theme_bw(base_size = 11) +
        theme(legend.position = "bottom", panel.grid.minor = element_blank(),
              panel.spacing = grid::unit(1.2, "lines"),
              plot.margin = margin(t = 8, r = 16, b = 8, l = 8),
              strip.text = element_text(size = 10, face = "bold"),
              plot.caption = element_text(size = 8.5, hjust = 0, lineheight = 1),
              plot.caption.position = "plot", plot.subtitle = element_text(size = 10))
    }
    check_inputs()
    if (file.exists(output)) stop("Refusing to overwrite output; choose a new output directory")
    if (!dir.create(output, recursive = TRUE)) stop("Cannot create new output directory")
    figures <- c("recall_qps", paste0(scenarios, "_recall_qps"))
    files <- c(as.vector(outer(figures, c("png", "pdf"), paste, sep = ".")),
               "plotted_points.csv", "source_summary.csv", "source_registration.json",
               "plot_metadata.json", "hash_manifest.csv")
    success <- FALSE
    on.exit({
      if (!success) {
        unlink(file.path(output, files))
        if (length(list.files(output, all.files = TRUE, no.. = TRUE)) == 0L) unlink(output, recursive = TRUE)
      }
    }, add = TRUE)
    old_options <- options(warn = 2)
    on.exit(options(old_options), add = TRUE)
    for (scenario in c("", scenarios)) {
      combined <- scenario == ""
      caption_text <- caption(if (combined) 165L else 105L)
      frame <- if (combined) points else points[points$scenario == scenario, ]
      plot <- make_plot(frame, caption_text)
      if (combined) {
        plot <- plot + facet_wrap(~scenario, ncol = 3, labeller = as_labeller(panel_labels)) +
          labs(title = "SIFT1B: Recall@10-QPS comparison",
               subtitle = paste(c(comparison_label, declaration$caption_note), collapse = "\n"))
      } else {
        plot <- plot +
          labs(title = paste0("SIFT1B: ", scenario_metadata[[scenario]]$title, " - Recall@10-QPS"),
               subtitle = paste(c(density_labels[[scenario]],
                                  paste(strwrap(scenario_metadata[[scenario]]$predicate, 105L), collapse = "\n"),
                                  declaration$caption_note), collapse = "\n"))
      }
      stem <- if (combined) "recall_qps" else paste0(scenario, "_recall_qps")
      height <- (if (combined) 8.5 else 5.5) + 0.16 * length(strsplit(caption_text, "\n")[[1]])
      for (extension in c("png", "pdf")) {
        ggsave(file.path(output, paste0(stem, ".", extension)), plot = plot,
               width = if (combined) 16 else 9, height = height, dpi = 180, bg = "white",
               device = if (extension == "pdf") grDevices::cairo_pdf else "png")
      }
    }
    write.csv(raw, file.path(output, "plotted_points.csv"), row.names = FALSE)
    if (!file.copy(inputs[["summary"]], file.path(output, "source_summary.csv")) ||
        !file.copy(inputs[["registration"]], file.path(output, "source_registration.json"))) {
      stop("Cannot preserve plotting input snapshots")
    }
    check_inputs()
    hashed_outputs <- setdiff(files, c("plot_metadata.json", "hash_manifest.csv"))
    output_hashes <- setNames(hash_files(file.path(output, hashed_outputs)), hashed_outputs)
    metadata <- list(
      schema_version = 1L, mode = "selectivity", dataset = declaration$dataset,
      corpus_count = declaration$corpus_count, comparison = declaration$comparison,
      series = series,
      scenarios = I(scenarios), scenario_metadata = scenario_metadata, engine_order = I(engine_order),
      engines = engines, caption_note = declaration$caption_note,
      source_rows = nrow(raw), plotted_points = nrow(raw), hash_algorithm = "md5",
      input_files = as.list(inputs), input_hashes = as.list(input_hashes),
      output_hashes = as.list(output_hashes),
      point_order = "Registered scenario, engine, ascending native L; never recall order",
      coordinate_export = "All input CSV fields preserved verbatim; only row order changes",
      recall_axis = c(0, 1), qps_axis = "log10", interpolated = FALSE,
      all_measured_points_retained = TRUE, diagnostic_timings_used = FALSE,
      uncertainty = "Observed repetition ranges, not confidence intervals",
      io_comparison = "SPTAG buffered versus PipeANN direct; not matched I/O",
      r_version = R.version.string, ggplot2_version = as.character(packageVersion("ggplot2"))
    )
    write_json(metadata, file.path(output, "plot_metadata.json"), auto_unbox = TRUE, pretty = TRUE, digits = NA)
    manifest_outputs <- setdiff(files, "hash_manifest.csv")
    manifest <- rbind(
      data.frame(role = "input", path = unname(inputs), md5 = unname(input_hashes)),
      data.frame(role = "output", path = manifest_outputs,
                 md5 = hash_files(file.path(output, manifest_outputs)))
    )
    write.csv(manifest, file.path(output, "hash_manifest.csv"), row.names = FALSE)
    check_inputs()
    success <- TRUE
    cat("Created five registered scenario panels and individual figures;",
        nrow(raw), "unaltered measured points in", output, "\n")
  }
  render_selectivity(args[[1]], args[[2]])
  quit(save = "no", status = 0L)
}
if (length(args) != 1L) stop("usage: plot_sift1b_official.R <run-directory>")
suppressPackageStartupMessages(library(ggplot2))
root <- normalizePath(args[[1]], mustWork = TRUE)
data <- read.csv(file.path(root, "summary.csv"), stringsAsFactors = FALSE)
required <- c("scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
              "recall", "qps", "qps_min", "qps_max", "candidate_count", "selectivity", "predicate")
if (!all(required %in% names(data)) || anyNA(data[required]) ||
    anyDuplicated(data[c("scenario", "engine", "L")])) stop("invalid comparison matrix")
if (any(!is.finite(data$qps) | data$qps <= 0) ||
    any(!is.finite(data$recall) | data$recall < 0 | data$recall > 1)) stop("invalid performance data")
titles <- c(unfilter = "Unfiltered", broad_tag = "Broad categorical filter",
            medium_tag = "Medium categorical filter", numeric = "Numeric-only filter",
            mixed_dnf = "Mixed categorical / numeric DNF", extreme_tag = "Extreme categorical filter")
if (!setequal(unique(data$scenario), names(titles))) stop("incomplete scenario set")
engine_names <- c(SPANN = "SPANN H5 (raw UInt8)", PipeANN = "PipeANN (official search-only, PQ32)")
colors <- c("SPANN H5 (raw UInt8)" = "#0072B2", "PipeANN (official search-only, PQ32)" = "#D55E00")
theme_set(theme_bw(base_size = 11))
for (scenario in names(titles)) {
  points <- data[data$scenario == scenario, ]
  if (!setequal(points$engine, names(engine_names))) stop(paste("missing engine:", scenario))
  if (any(table(points$engine) < 2L)) stop(paste("not enough curve points:", scenario))
  for (field in c("queries", "repeats", "threads", "cpu_nodes", "candidate_count", "selectivity")) {
    if (length(unique(points[[field]])) != 1L) stop(paste("inconsistent", field, scenario))
  }
  points <- points[order(points$engine, points$L), ]
  points$algorithm <- factor(points$engine, levels = names(engine_names), labels = engine_names)
  selectivity <- format(100 * points$selectivity[[1]], scientific = FALSE, trim = TRUE, digits = 10)
  candidates <- format(points$candidate_count[[1]], scientific = FALSE, big.mark = ",", trim = TRUE)
  caption <- paste(
    sprintf("Full SIFT1B; top-10; %s identical queries warmed at each point; %s query thread; NUMA node(s) %s.",
            points$queries[[1]], points$threads[[1]], points$cpu_nodes[[1]]),
    sprintf("Median of %s serial passes; spans are min-max, not confidence intervals. All points are retained.",
            points$repeats[[1]]),
    "PipeANN: read-only/no-mapping, pipeline 32, unfiltered mem_L=10, filtered auto/mem_L=0.",
    "Native I/O differs: SPANN buffered, PipeANN direct. This is not a matched-I/O algorithm-only comparison.",
    sep = "\n"
  )
  plot <- ggplot(points, aes(recall, qps, color = algorithm, shape = algorithm, group = algorithm)) +
    geom_linerange(aes(ymin = qps_min, ymax = qps_max), linewidth = 0.4, alpha = 0.5) +
    geom_path(linewidth = 0.7) + geom_point(size = 2.6) +
    scale_color_manual(values = colors, drop = FALSE) +
    scale_shape_manual(values = c(16, 17), drop = FALSE) +
    scale_x_continuous(limits = c(0, 1), breaks = seq(0, 1, 0.1),
                       labels = function(x) sprintf("%.0f%%", 100 * x),
                       expand = expansion(mult = c(0.015, 0.02))) +
    scale_y_log10(labels = function(x) format(x, scientific = FALSE, big.mark = ",", trim = TRUE)) +
    labs(title = paste("SIFT1B:", titles[[scenario]]),
         subtitle = paste0(points$predicate[[1]], "\nSelectivity ", selectivity, "%; ", candidates, " eligible vectors"),
         x = "Recall@10", y = "Queries per second (log scale)", color = NULL, shape = NULL, caption = caption) +
    theme(legend.position = "bottom", panel.grid.minor = element_blank(),
          plot.caption = element_text(size = 8, hjust = 0), plot.subtitle = element_text(size = 10))
  for (extension in c("png", "pdf")) {
    output <- file.path(root, "plots", paste0(scenario, "_recall_qps.", extension))
    if (file.exists(output)) stop(paste("refusing to replace plot:", output))
    ggsave(output, plot = plot, width = 8.5, height = 6, dpi = 180)
    message(output)
  }
}

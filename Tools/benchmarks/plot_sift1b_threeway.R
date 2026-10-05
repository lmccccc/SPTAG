#!/usr/bin/env Rscript

# Rscript plot_sift1b_threeway.R <campaign-directory> <new-output-directory> --stage single|complete
# Availability is an exceptions log; recall_target/threads scope records (blanks are wildcards).
# Unavailable scopes override measurements; explicit available records are optional.
# Shared operating measurements at R90/R95 are labeled views, not independent repeats.
# Peak CSV measurements retain the source strings; only derived peak fields are added.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 4L || args[[3]] != "--stage" ||
    !args[[4]] %in% c("single", "complete")) {
  stop(paste("usage: plot_sift1b_threeway.R <campaign-directory>",
             "<new-output-directory> --stage single|complete"), call. = FALSE)
}
suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(jsonlite))

render_threeway <- function(root, output, stage) {
  fail <- function(...) stop(paste0(...), call. = FALSE)
  exists <- function(path) {
    link <- Sys.readlink(path)
    file.exists(path) || (!is.na(link) && nzchar(link))
  }
  if (exists(output)) fail("Refusing to overwrite output; choose a new output directory")
  old_options <- options(warn = 2)
  on.exit(options(old_options), add = TRUE)
  root <- normalizePath(root, mustWork = TRUE)
  inputs <- c(registration = "registration.json", single_summary = "single_summary.csv",
              availability = "availability.csv")
  if (stage == "complete") inputs <- c(inputs, throughput_summary = "throughput_summary.csv")
  inputs <- setNames(normalizePath(file.path(root, inputs), mustWork = TRUE), names(inputs))
  hash_files <- function(paths) {
    hashes <- unname(tools::md5sum(paths))
    if (anyNA(hashes)) fail("Cannot hash plotting inputs or outputs")
    hashes
  }
  input_hashes <- setNames(hash_files(inputs), names(inputs))
  check_inputs <- function() {
    if (!identical(hash_files(inputs), unname(input_hashes))) {
      fail("Frozen plotting inputs changed during rendering")
    }
  }
  text <- function(x, nonempty = TRUE) {
    is.character(x) && length(x) == 1L && !is.na(x) &&
      (!nonempty || nzchar(trimws(x)))
  }
  number <- function(x) is.numeric(x) && length(x) == 1L && is.finite(x)
  integer_value <- function(x, minimum = 0, maximum = .Machine$integer.max) {
    number(x) && x == floor(x) && x >= minimum && x <= maximum
  }
  object <- function(x) {
    is.list(x) && length(x) > 0L && !is.null(names(x)) &&
      !anyDuplicated(names(x)) && all(nzchar(trimws(names(x))))
  }
  array_equals <- function(x, expected, scalar) {
    is.list(x) && is.null(names(x)) && length(x) == length(expected) &&
      all(vapply(x, scalar, logical(1))) &&
      all(unlist(x, use.names = FALSE) == expected)
  }
  scenarios <- c("unfilter", "broad_tag", "medium_tag", "sel_01pct", "mixed_dnf")
  engines <- c("SPTAG_adaptive", "PipeANN", "Filtered_DiskANN")
  recall_targets <- c(0.90, 0.95)
  thread_grid <- c(1, 2, 4, 8, 16, 24, 32, 40, 48, 64, 96, 128, 192)
  labels <- c(SPTAG_adaptive = "SPTAG adaptive", PipeANN = "PipeANN",
              Filtered_DiskANN = "Filtered-DiskANN")
  colors <- c(SPTAG_adaptive = "#009E73", PipeANN = "#D55E00", Filtered_DiskANN = "#0072B2")
  shapes <- c(SPTAG_adaptive = 17, PipeANN = 18, Filtered_DiskANN = 16)
  line_types <- c(SPTAG_adaptive = "solid", PipeANN = "dotdash", Filtered_DiskANN = "dashed")
  declaration <- fromJSON(inputs[["registration"]], simplifyVector = FALSE)
  throughput_labels <- labels
  if (!is.null(declaration$native_pipeann_correctness)) {
    if (!object(declaration$native_pipeann_correctness) ||
        !identical(declaration$native_pipeann_correctness$mode,
                   "retain-unexpanded-pages-before-ring-reuse-v1")) {
      fail("Invalid PipeANN native correctness declaration")
    }
    throughput_labels[["PipeANN"]] <- "PipeANN (page-lifetime fix)"
  }
  native_short_results <- !is.null(declaration$native_pipeann_result_policy)
  if (native_short_results &&
      (!object(declaration$native_pipeann_result_policy) ||
       !identical(declaration$native_pipeann_result_policy$mode, "native-count-prefix-v1") ||
       !identical(declaration$native_pipeann_result_policy$authorization, "allow-native-short-results") ||
       !number(declaration$native_pipeann_result_policy$schema_version) ||
       declaration$native_pipeann_result_policy$schema_version != 2)) {
    fail("Invalid native returned-prefix protocol declaration")
  }
  supplement <- declaration$unfiltered_supplement
  supplemented <- !is.null(supplement)
  if (supplemented) {
    if (!object(supplement) || !identical(supplement$mode, "fixed-grid-unfilter-only-v1") ||
        !text(supplement$previous_directory) || !object(supplement$search_grids) ||
        !setequal(names(supplement$search_grids), c("PipeANN", "Filtered_DiskANN")) ||
        is.null(declaration$native_pipeann_correctness) || !native_short_results) {
      fail("Invalid unfiltered supplement declaration")
    }
    for (grid in supplement$search_grids) {
      if (!is.list(grid) || !is.null(names(grid)) || !length(grid) ||
          !all(vapply(grid, integer_value, logical(1), minimum = 10)) ||
          !identical(unlist(grid), sort(unique(unlist(grid))))) {
        fail("Invalid supplemental search grid")
      }
    }
  }
  spann_refresh <- declaration$spann_single_refresh
  refreshed_spann <- !is.null(spann_refresh)
  if (refreshed_spann) {
    if (stage != "single" || !object(spann_refresh) ||
        !identical(spann_refresh$mode, "optimized-main-single-v1") ||
        !text(spann_refresh$previous_directory) || !text(spann_refresh$native_binary) ||
        !text(spann_refresh$native_binary_sha256) ||
        !grepl("^[0-9a-f]{64}$", spann_refresh$native_binary_sha256) ||
        !is.list(spann_refresh$nprobe) || !is.null(names(spann_refresh$nprobe)) ||
        !length(spann_refresh$nprobe) ||
        !all(vapply(spann_refresh$nprobe, integer_value, logical(1), minimum = 1)) ||
        !identical(unlist(spann_refresh$nprobe), sort(unique(unlist(spann_refresh$nprobe))))) {
      fail("Invalid SPANN single-thread refresh declaration; old throughput cannot be relabeled")
    }
    labels[["SPTAG_adaptive"]] <- "SPTAG (optimized)"
  }
  constants <- c(schema_version = 1, corpus_count = 1000000000, query_count = 1000,
                 recall_target = 0.95, throughput_min_seconds = 30, throughput_core_budget = 96)
  if (!object(declaration) || !identical(declaration[["dataset"]], "SIFT1B") ||
      !identical(declaration[["metric"]], "squared L2") ||
      !all(vapply(names(constants), function(key) {
        number(declaration[[key]]) && declaration[[key]] == constants[[key]]
      }, logical(1))) ||
      !array_equals(declaration[["scenarios"]], scenarios, text) ||
      !array_equals(declaration[["engines"]], engines, text) ||
      !array_equals(declaration[["recall_targets"]], recall_targets, number) ||
      !array_equals(declaration[["throughput_thread_grid"]], thread_grid, number)) {
    fail("Invalid registration: require schema 1, the fixed SIFT1B protocol, five scenarios, ",
         "three exact engine IDs, recall_targets=[0.90,0.95] with primary recall_target=0.95, ",
         "and registered thread grid")
  }
  target_labels <- setNames(paste0("Recall >= ", 100 * recall_targets, "%",
                                   ifelse(recall_targets == declaration$recall_target, " (primary)", "")),
                            as.character(recall_targets))
  for (field in c("caption_note", "aio_limit_note")) {
    if (!is.null(declaration[[field]]) && !text(declaration[[field]], nonempty = FALSE)) {
      fail("Invalid registration ", field, ": expected a string")
    }
  }
  scenario_metadata <- declaration[["scenario_metadata"]]
  if (!object(scenario_metadata) || !setequal(names(scenario_metadata), scenarios)) {
    fail("Invalid scenario_metadata: require exactly the five registered scenarios")
  }
  for (scenario in scenarios) {
    entry <- scenario_metadata[[scenario]]
    if (!object(entry) || !text(entry[["title"]]) || !text(entry[["predicate"]]) ||
        !integer_value(entry[["candidate_count"]], maximum = declaration$corpus_count) ||
        !number(entry[["selectivity"]]) || entry$selectivity < 0 || entry$selectivity > 1 ||
        abs(entry$selectivity - entry$candidate_count / declaration$corpus_count) > 1e-12 ||
        (scenario == "unfilter" && entry$candidate_count != declaration$corpus_count)) {
      fail("Invalid scenario_metadata predicate/count/selectivity: ", scenario)
    }
  }

  read_table <- function(key, required, blank = character(), allow_empty = FALSE) {
    raw <- read.csv(inputs[[key]], colClasses = "character", check.names = FALSE,
                    na.strings = character(), stringsAsFactors = FALSE, fill = FALSE)
    if (!all(required %in% names(raw)) || anyDuplicated(names(raw)) ||
        any(!nzchar(trimws(names(raw)))) || (!allow_empty && nrow(raw) == 0L) ||
        anyNA(raw[required]) ||
        any(!vapply(raw[setdiff(required, blank)], function(x) {
          all(nzchar(trimws(x)))
        }, logical(1)))) {
      fail("Invalid ", basename(inputs[[key]]), " schema or missing measurement fields")
    }
    raw
  }
  numeric_fields <- function(raw, fields, table, optional = character()) {
    points <- raw
    for (field in fields) {
      blank <- !nzchar(trimws(raw[[field]]))
      points[[field]] <- suppressWarnings(as.numeric(raw[[field]]))
      invalid <- !is.finite(points[[field]]) & !(field %in% optional & blank)
      if (any(invalid)) {
        fail("Invalid numeric measurement in ", table, ": ", field,
             " (row ", which(invalid)[[1]], ")")
      }
    }
    points
  }
  integers <- function(points, fields, table, minimum = 1, maximum = .Machine$integer.max) {
    for (field in fields) {
      if (any(points[[field]] != floor(points[[field]]) |
              points[[field]] < minimum | points[[field]] > maximum)) {
        fail("Invalid integer measurement in ", table, ": ", field)
      }
    }
  }
  identities <- function(points, table) {
    if (any(!points$scenario %in% scenarios) || any(!points$engine %in% engines)) {
      fail("Unregistered scenario or engine in ", table)
    }
    if (any(points$engine == "Filtered_DiskANN" & points$scenario == "mixed_dnf")) {
      fail("Filtered-DiskANN mixed_dnf/numeric predicates are unsupported; no fabricated measurements")
    }
  }
  performance <- function(points, table) {
    if (any(points$recall_min < 0 | points$recall_max > 1 |
            points$recall_min > points$recall | points$recall > points$recall_max)) {
      fail("Invalid recall ranges in ", table)
    }
    if (any(points$qps_min <= 0 | points$qps_min > points$qps | points$qps > points$qps_max)) {
      fail("Invalid QPS ranges in ", table)
    }
  }
  duplicates <- function(points, keys, table) {
    if (anyDuplicated(points[keys])) fail("Duplicate measured points in ", table)
  }
  factor_frame <- function(points) {
    points$scenario <- factor(points$scenario, levels = scenarios)
    points$engine <- factor(points$engine, levels = engines)
    points
  }
  availability <- read_table("availability", c("phase", "scenario", "engine", "status", "reason"),
                             blank = "reason", allow_empty = TRUE)
  statuses <- c("available", "unsupported_predicate", "recall_target_unmet", "aio_limit",
                "invalid_output", "failed", "blocked")
  if (any(!availability$phase %in% c("single", "throughput")) ||
      any(!availability$scenario %in% scenarios) || any(!availability$engine %in% engines) ||
      any(!availability$status %in% statuses) ||
      any(availability$status != "available" & !nzchar(trimws(availability$reason))) ||
      anyDuplicated(availability)) {
    fail("Invalid availability.csv phase, scenario, engine, status, reason or duplicate record")
  }
  if (any(availability$engine == "Filtered_DiskANN" & availability$scenario == "mixed_dnf" &
          availability$status != "unsupported_predicate")) {
    fail("Filtered-DiskANN mixed_dnf availability must be unsupported_predicate")
  }
  availability_scope <- availability
  scope_fields <- intersect(c("recall_target", "threads"), names(availability))
  if (anyNA(availability[scope_fields])) fail("Invalid availability.csv optional scope fields")
  availability_scope <- numeric_fields(availability_scope, scope_fields, "availability.csv",
                                       optional = scope_fields)
  for (field in setdiff(c("recall_target", "threads"), scope_fields)) {
    availability_scope[[field]] <- rep(NA_real_, nrow(availability_scope))
  }
  if (any(!is.na(availability_scope$recall_target) &
          !availability_scope$recall_target %in% recall_targets)) {
    fail("Invalid availability.csv recall_target: outside registered targets")
  }
  if (any(!is.na(availability_scope$threads) &
          (!availability_scope$threads %in% thread_grid |
           (availability_scope$phase == "single" & availability_scope$threads != 1)))) {
    fail("Invalid availability.csv threads: require registered concurrency (single threads=1)")
  }
  if (anyDuplicated(availability_scope)) fail("Invalid availability.csv duplicate scoped record")
  records <- function(phase, scenario, engine, target = NULL, threads = NULL) {
    selected <- availability_scope$phase == phase & availability_scope$scenario == scenario &
      availability_scope$engine == engine
    if (!is.null(target)) {
      selected <- selected & (is.na(availability_scope$recall_target) |
                                availability_scope$recall_target == target)
    }
    if (!is.null(threads)) {
      selected <- selected & (is.na(availability_scope$threads) | availability_scope$threads == threads)
    }
    availability_scope[selected, , drop = FALSE]
  }
  check_availability <- function(points, phase) {
    for (i in seq_len(nrow(points))) {
      point <- points[i, , drop = FALSE]
      target <- if (phase == "throughput") point$recall_target else NULL
      entries <- records(phase, point$scenario, point$engine, target, point$threads)
      if (any(entries$status != "available")) {
        fail("Availability disagrees with measured rows: ", phase, "/", point$scenario, "/",
             point$engine, if (!is.null(target)) paste0("/recall_target=", target),
             "/threads=", point$threads, "; authoritative unavailable scope")
      }
    }
    entries <- availability_scope[availability_scope$phase == phase &
                                    availability_scope$status == "available", , drop = FALSE]
    for (i in seq_len(nrow(entries))) {
      entry <- entries[i, , drop = FALSE]
      selected <- points$scenario == entry$scenario & points$engine == entry$engine
      if (phase == "throughput" && !is.na(entry$recall_target)) {
        selected <- selected & points$recall_target == entry$recall_target
      }
      if (!is.na(entry$threads)) selected <- selected & points$threads == entry$threads
      if (!any(selected)) {
        fail("Availability disagrees with measured rows: available ", phase, " scope has no points: ",
             entry$scenario, "/", entry$engine)
      }
    }
    if (phase == "single") {
      for (scenario in scenarios) for (engine in engines[1:2]) {
        if (!any(points$scenario == scenario & points$engine == engine)) {
          fail("Missing reused historical single-thread curve: ", scenario, "/", engine)
        }
      }
    }
  }

  single_required <- c("scenario", "engine", "L", "queries", "repeats", "threads", "cpu_nodes",
                       "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
                       "candidate_count", "selectivity", "predicate")
  single_raw <- read_table("single_summary", single_required)
  single <- numeric_fields(single_raw,
                           setdiff(single_required, c("scenario", "engine", "cpu_nodes", "predicate")),
                           "single_summary.csv")
  identities(single, "single_summary.csv")
  integers(single, c("L", "queries", "repeats", "threads"), "single_summary.csv")
  integers(single, "candidate_count", "single_summary.csv", minimum = 0,
           maximum = declaration$corpus_count)
  performance(single, "single_summary.csv")
  if (any(single$queries != declaration$query_count | single$threads != 1)) {
    fail("Single-thread rows require queries=1000 and threads=1")
  }
  if (supplemented && !"measurement_series" %in% names(single)) {
    fail("Supplement requires explicit single-thread measurement_series")
  }
  if (!"measurement_series" %in% names(single)) single$measurement_series <- "baseline"
  current_pipeann <- single$measurement_series == "pipeann_current"
  optimized_spann <- single$measurement_series == "spann_optimized"
  if (any(!single$measurement_series %in% c("baseline", "pipeann_current", "spann_optimized")) ||
      (!supplemented && any(current_pipeann)) ||
      (!refreshed_spann && any(optimized_spann)) ||
      any(optimized_spann & single$engine != "SPTAG_adaptive") ||
      any(current_pipeann & (single$engine != "PipeANN" | single$scenario != "unfilter"))) {
    fail("Invalid single-thread measurement_series")
  }
  if (refreshed_spann) {
    if (any(single$engine == "SPTAG_adaptive" & !optimized_spann) ||
        any(optimized_spann & single$repeats != 2) ||
        !all(vapply(scenarios, function(scenario) {
          setequal(single$L[optimized_spann & single$scenario == scenario], unlist(spann_refresh$nprobe))
        }, logical(1)))) {
      fail("SPANN refresh requires the complete declared native grid and two fresh repetitions")
    }
  }
  if (supplemented &&
      !setequal(single$L[current_pipeann], unlist(supplement$search_grids$PipeANN))) {
    fail("Current PipeANN points differ from the registered supplemental grid")
  }
  duplicates(single, c("scenario", "engine", "L", "measurement_series"), "single_summary.csv")
  for (scenario in scenarios) {
    panel <- single[single$scenario == scenario, , drop = FALSE]
    entry <- scenario_metadata[[scenario]]
    if (any(panel$predicate != entry$predicate | panel$candidate_count != entry$candidate_count |
            panel$selectivity < 0 | panel$selectivity > 1 |
            abs(panel$selectivity - entry$selectivity) > 1e-12)) {
      fail("Single-thread predicate/count/selectivity disagrees with scenario_metadata: ", scenario)
    }
  }
  if ("measurement_reused" %in% names(single)) {
    reused <- tolower(trimws(single$measurement_reused))
    if (refreshed_spann) {
      if (any(optimized_spann & !reused %in% c("false", "0")) ||
          any(!optimized_spann & !reused %in% c("true", "1"))) {
        fail("Invalid measurement_reused: refreshed SPANN is fresh; all peer measurements are retained")
      }
    } else if (any(!reused %in% c("", "true", "false", "1", "0")) ||
        any(single$engine != "Filtered_DiskANN" & !current_pipeann & reused %in% c("false", "0")) ||
        any(current_pipeann & !reused %in% c("false", "0"))) {
      fail("Invalid measurement_reused: only declared current PipeANN points may be fresh")
    }
  } else if (any(current_pipeann) || refreshed_spann) {
    fail("Current PipeANN and refreshed SPANN points require explicit measurement_reused")
  }
  if ("io_mode" %in% names(single)) {
    specified <- nzchar(trimws(single$io_mode))
    expected <- ifelse(single$engine == "SPTAG_adaptive", "buffered", "direct")
    if (any(specified & single$io_mode != expected)) fail("Invalid native io_mode in single_summary.csv")
  }
  check_availability(single, "single")
  single <- factor_frame(single[order(match(single$scenario, scenarios),
                                     match(single$engine, engines), single$L), , drop = FALSE])
  single$series_key <- ifelse(single$measurement_series == "pipeann_current",
                              "PipeANN_current", as.character(single$engine))
  single_keys <- engines
  single_labels <- labels
  single_colors <- colors
  single_shapes <- shapes
  single_lines <- line_types
  if (supplemented) {
    single_keys <- c(engines[1:2], "PipeANN_current", engines[[3]])
    single_labels <- c(labels, PipeANN_current = "PipeANN (current corrected client)")
    single_labels[["PipeANN"]] <- "PipeANN (historical)"
    single_colors <- c(colors, PipeANN_current = colors[["PipeANN"]])
    single_shapes <- c(shapes, PipeANN_current = 5)
    single_lines <- c(line_types, PipeANN_current = "longdash")
  }
  single$series_key <- factor(single$series_key, levels = single_keys)

  throughput <- throughput_raw <- peaks <- peak_raw <- NULL
  if (stage == "complete") {
    optional_metrics <- c("mean_latency_us", "p50_latency_us", "p99_latency_us", "mean_ios")
    throughput_required <- c("scenario", "engine", "threads", "L", "repeats", "recall_target",
                             "recall", "recall_min", "recall_max", "qps", "qps_min", "qps_max",
                             "seconds_min", "queries_min", optional_metrics, "cpu_nodes", "memory_nodes")
    count_fields <- c("measured_queries_total", "returned_neighbors_total", "missing_neighbors_total",
                      "underfilled_queries_total", "returned_per_query", "underfilled_fraction")
    if (native_short_results) {
      throughput_required <- c(throughput_required, count_fields, "native_result_schemas")
    }
    throughput_raw <- read_table("throughput_summary", throughput_required,
                                 blank = optional_metrics, allow_empty = TRUE)
    throughput <- numeric_fields(
      throughput_raw, setdiff(throughput_required, c("scenario", "engine", "cpu_nodes", "memory_nodes",
                                                    "native_result_schemas")),
      "throughput_summary.csv", optional = optional_metrics)
    identities(throughput, "throughput_summary.csv")
    integers(throughput, c("threads", "L", "repeats"), "throughput_summary.csv")
    integers(throughput, "queries_min", "throughput_summary.csv",
             minimum = declaration$query_count, maximum = 2^53 - 1)
    if (native_short_results) {
      integers(throughput, count_fields[1:4], "native return counts", minimum = 0, maximum = 2^53 - 1)
      if (any(throughput$measured_queries_total < throughput$queries_min * throughput$repeats |
              throughput$returned_neighbors_total + throughput$missing_neighbors_total !=
                10 * throughput$measured_queries_total |
              throughput$underfilled_queries_total > throughput$measured_queries_total |
              throughput$missing_neighbors_total < throughput$underfilled_queries_total |
              throughput$missing_neighbors_total > 10 * throughput$underfilled_queries_total |
              abs(throughput$returned_per_query -
                    throughput$returned_neighbors_total / throughput$measured_queries_total) > 1e-12 |
              abs(throughput$underfilled_fraction -
                    throughput$underfilled_queries_total / throughput$measured_queries_total) > 1e-12 |
              !throughput$native_result_schemas %in% c("1", "2", "1,2") |
              (throughput$engine != "PipeANN" &
                 (throughput$native_result_schemas != "1" | throughput$missing_neighbors_total != 0)))) {
        fail("Invalid native returned/missing-neighbor accounting")
      }
    }
    performance(throughput, "throughput_summary.csv")
    if (any(!throughput$threads %in% thread_grid)) fail("Throughput threads are outside the registered grid")
    if (any(!throughput$recall_target %in% recall_targets)) {
      fail("Throughput recall_target disagrees with registered recall_targets")
    }
    if (any(throughput$recall_min < throughput$recall_target)) {
      fail("Throughput recall_min is below recall_target; invalid points cannot be silently filtered")
    }
    if (any(throughput$seconds_min < declaration$throughput_min_seconds)) {
      fail("Throughput seconds_min is below throughput_min_seconds; insufficient duration")
    }
    for (field in optional_metrics) {
      if (any(throughput[[field]] < 0, na.rm = TRUE)) fail("Invalid nonnegative metric: ", field)
    }
    if (any(throughput$p50_latency_us > throughput$p99_latency_us, na.rm = TRUE)) {
      fail("Invalid latency percentiles: p50_latency_us exceeds p99_latency_us")
    }
    duplicates(throughput, c("scenario", "engine", "recall_target", "threads", "L"), "throughput_summary.csv")
    check_availability(throughput, "throughput")
    ordering <- order(throughput$recall_target, match(throughput$scenario, scenarios),
                      match(throughput$engine, engines), throughput$L, throughput$threads)
    throughput <- factor_frame(throughput[ordering, , drop = FALSE])
    throughput_raw <- throughput_raw[ordering, , drop = FALSE]
    chosen <- integer()
    peak_facts <- data.frame(
      largest_tested_threads = numeric(), peak_at_largest_tested_threads = logical(),
      maximum_at_largest_tested_threads = logical(), boundary_censored = logical(),
      resource_limited = logical(), saturation_proven = logical(),
      limitation_statuses = character(), limitation_reasons = character())
    for (target in recall_targets) for (scenario in scenarios) for (engine in engines) {
      indices <- which(throughput$scenario == scenario & throughput$engine == engine &
                         throughput$recall_target == target)
      if (length(indices) == 0L) next
      block <- throughput[indices, , drop = FALSE]
      pick <- order(-block$qps, block$threads, block$L)[[1]]
      chosen <- c(chosen, indices[[pick]])
      largest <- max(block$threads)
      boundary <- any(block$qps == max(block$qps) & block$threads == largest)
      limitations <- records("throughput", scenario, engine, target)
      limitations <- limitations[limitations$status != "available", , drop = FALSE]
      peak_facts <- rbind(peak_facts, data.frame(
        largest_tested_threads = largest,
        peak_at_largest_tested_threads = block$threads[[pick]] == largest,
        maximum_at_largest_tested_threads = boundary, boundary_censored = boundary,
        resource_limited = any(limitations$status %in% c("aio_limit", "blocked")),
        saturation_proven = FALSE,
        limitation_statuses = paste(unique(limitations$status), collapse = " | "),
        limitation_reasons = paste(unique(limitations$reason), collapse = " | ")))
    }
    peak_raw <- cbind(throughput_raw[chosen, , drop = FALSE], peak_facts)
    peaks <- cbind(throughput[chosen, , drop = FALSE], peak_facts)
    peaks$point_label <- if (nrow(peaks)) {
      paste0("t=", peaks$threads, "; L=", peaks$L,
             ifelse(peaks$boundary_censored, "\nboundary-censored", "\nobserved interior peak"))
    } else character()
  }

  wrap <- function(x, width) paste(strwrap(x, width = width), collapse = "\n")
  annotations <- function(points, phase, target = NULL) {
    if (!is.null(target)) points <- points[points$recall_target == target, , drop = FALSE]
    setNames(vapply(scenarios, function(scenario) {
      notes <- character()
      for (engine in engines) {
        entries <- records(phase, scenario, engine, target)
        present <- any(points$scenario == scenario & points$engine == engine)
        if (!present) {
          unavailable <- unique(entries$status[entries$status != "available"])
          note <- if (scenario == "mixed_dnf" && engine == "Filtered_DiskANN") {
            "Filtered-DiskANN unavailable:\nunsupported mixed DNF / numeric"
          } else if (!is.null(target) && target == 0.95 && scenario == "medium_tag" && engine == "PipeANN") {
            "PipeANN: R95 unavailable\nwithin registered native grid"
          } else {
            paste0(labels[[engine]], " unavailable: ",
                   if (length(unavailable)) paste(unavailable, collapse = "/") else "no qualifying measurement")
          }
          notes <- c(notes, note)
        } else if (any(entries$status %in% c("aio_limit", "blocked"))) {
          notes <- c(notes, paste0(labels[[engine]], ": concurrency limited (",
                                   paste(unique(entries$status[entries$status != "available"]),
                                         collapse = "/"), ")"))
        }
      }
      paste(vapply(notes, wrap, character(1), width = 38), collapse = "\n")
    }, character(1)), scenarios)
  }
  single_annotations <- annotations(single, "single")
  throughput_annotations <- if (stage == "complete") {
    setNames(lapply(recall_targets, function(target) annotations(throughput, "throughput", target)),
             as.character(recall_targets))
  } else NULL
  panel_labels <- function(notes) {
    setNames(vapply(scenarios, function(scenario) {
      entry <- scenario_metadata[[scenario]]
      paste(c(wrap(entry$title, 38),
              paste0("Actual selectivity: ",
                     format(100 * entry$selectivity, scientific = FALSE, trim = TRUE, digits = 10), "%"),
              paste0(format(entry$candidate_count, big.mark = ",", scientific = FALSE, trim = TRUE),
                     " eligible"),
              if (nzchar(notes[[scenario]])) notes[[scenario]]), collapse = "\n")
    }, character(1)), scenarios)
  }
  common_notes <- c(
    if (refreshed_spann)
      paste("Optimized SPANN single-thread points are freshly measured; prior SPANN points are replaced in this new figure, not rescaled.",
            "All DiskANN and PipeANN measured values are retained unchanged; not a fresh paired rerun.",
            if (supplemented) "Historical and current corrected PipeANN clients remain separate series, never joined or pooled.")
    else if (supplemented)
      "All original single-thread points are retained unchanged. Supplemental unfiltered PipeANN points use the current corrected client, shown as a separate hollow-diamond series; historical and current clients are never joined or pooled. DiskANN extends the same graph."
    else
      "SPANN (SPTAG adaptive) and PipeANN single-thread curves reuse historical September 24 (Sep24) measurements unchanged; not a fresh paired rerun.",
    "Historical PipeANN medium_tag peaks at Recall 0.9016: R95 unavailable within the registered native grid; R90 remains a valid comparison only where current measured repetitions meet its target.",
    "Full SIFT1B, squared L2, Recall@10. Each phase repeats a fixed 1000-query hot working set; no global page-cache drops.",
    "Native I/O differs: SPANN buffered; PipeANN and Filtered-DiskANN direct. NOT a matched-I/O algorithm-only comparison.",
    "L is engine-native: SPANN nprobe; PipeANN and Filtered-DiskANN searchL, not equal work. No smoothing, interpolation or extrapolation.",
    "Filtered-DiskANN: original categorical-focused R64, L1, FilteredL100, PQ32 index; no disk vector quantization. Unfiltered and single categorical labels only; mixed DNF/numeric predicates are not native.",
    "Existing system settings are kept unchanged. Native DiskANN needs 1024 AIO slots per worker; recorded live headroom determines which registered thread counts are feasible.",
    "Report resource-limited maximum observed throughput, NOT an unconstrained algorithm maximum; no claim of a saturated theoretical maximum.",
    "Spans show observed repetition ranges, not confidence intervals. Unavailable curves are not fabricated; full availability/concurrency reasons are retained in plot_metadata.json.",
    if (native_short_results)
      "PipeANN scores only the native API-returned prefix. Missing neighbors contribute zero to Recall@10; its denominator stays queries times 10. No padding, output repair or output-dependent budget widening. Per-query counts and aggregate underfill rates are retained.",
    if (!is.null(declaration$aio_limit_note) && nzchar(declaration$aio_limit_note))
      paste0("Campaign AIO note: ", declaration$aio_limit_note),
    declaration$caption_note)
  caption <- function(extra) paste(vapply(c(extra, common_notes), wrap, character(1), width = 215),
                                   collapse = "\n")
  captions <- list(single_thread_recall_qps = caption(
    "Every single-thread measured point is retained. Segments connect points in ascending native L order within the same client series, never sorted by recall."))
  if (stage == "complete") {
    captions$throughput_scaling <- caption(paste(
      "Recall >= 90% (R90) and Recall >= 95% (R95, primary) are separate views. Each row must meet its own recall_target and seconds_min >= 30; QPS is the supplied repetition median.",
      "Identical data at the same native operating budget may appear in both target views: these are NOT independent repeats and are never pooled.",
      "Lines keep native L fixed; all measured points are shown. The CPU budget is 96 cores (vertical line); 128/192 query threads oversubscribe it."))
    captions$throughput_peak <- caption(paste(
      "Highest observed median QPS per scenario/engine/recall_target; ties choose fewer threads, then smaller native L. Labels retain threads/L. R95 (0.95) is the primary target; R90 (0.90) remains separate.",
      "Identical data at the same native operating budget may appear in both target views: these are NOT independent repeats and are never pooled.",
      "Boundary-censored means a maximum reaches the largest successfully measured thread count, including ties; blocked attempts are not measured points.",
      "Interior peaks also do not prove saturation. Every point meets its own recall_target and seconds_min >= 30 within the 96-core CPU budget; 128/192 threads oversubscribe."))
  }
  scaffold <- data.frame(scenario = factor(scenarios, levels = scenarios))
  qps_limits <- function(points) {
    if (nrow(points)) range(points$qps_min, points$qps_max) else c(1, 10)
  }
  common_plot <- function(plot, points, notes, title, subtitle, caption_text, target_facets = FALSE,
                          engine_labels = labels, legend_keys = engines,
                          plot_colors = colors, plot_shapes = shapes) {
    if (target_facets) {
      panels <- expand.grid(scenario = factor(scenarios, levels = scenarios), recall_target = recall_targets)
      descriptions <- lapply(notes, panel_labels)
      label_panels <- function(frame) {
        list(vapply(seq_len(nrow(frame)), function(i) {
          target <- as.character(frame$recall_target[[i]])
          scenario <- as.character(frame$scenario[[i]])
          paste(target_labels[[target]], descriptions[[target]][[scenario]], sep = "\n")
        }, character(1)))
      }
      facets <- facet_wrap(vars(recall_target, scenario), ncol = 5, drop = FALSE, labeller = label_panels)
    } else {
      panels <- scaffold
      facets <- facet_wrap(~scenario, ncol = 5, drop = FALSE, labeller = as_labeller(panel_labels(notes)))
    }
    plot + geom_blank(data = panels, inherit.aes = FALSE) + facets +
      scale_colour_manual(values = plot_colors, limits = legend_keys,
                          labels = engine_labels[legend_keys], drop = FALSE) +
      scale_shape_manual(values = plot_shapes, limits = legend_keys,
                         labels = engine_labels[legend_keys], drop = FALSE) +
      scale_y_log10(limits = qps_limits(points), expand = expansion(mult = c(0.08, 0.24)),
                    labels = function(x) format(x, scientific = FALSE, big.mark = ",", trim = TRUE)) +
      coord_cartesian(clip = "off") +
      labs(title = title, subtitle = subtitle, y = "Queries per second (log scale)",
           colour = NULL, shape = NULL, linetype = NULL, caption = caption_text) +
      theme_bw(base_size = 12) +
      theme(legend.position = "bottom", panel.grid.minor = element_blank(),
            panel.spacing = grid::unit(1.2, "lines"),
            strip.text = element_text(size = 9.5, face = "bold", lineheight = 0.95),
            plot.caption = element_text(size = 9, hjust = 0, lineheight = 1),
            plot.caption.position = "plot", plot.subtitle = element_text(size = 11),
            plot.margin = margin(t = 10, r = 18, b = 10, l = 10))
  }
  paths <- function(points, fields) {
    key <- do.call(paste, c(points[fields], sep = "\r"))
    points[duplicated(key) | duplicated(key, fromLast = TRUE), , drop = FALSE]
  }
  single_plot <- ggplot(single, aes(recall, qps, colour = series_key, shape = series_key,
                                    linetype = series_key, group = series_key)) +
    geom_linerange(aes(ymin = qps_min, ymax = qps_max), linewidth = 0.4,
                   alpha = 0.45, show.legend = FALSE) +
    geom_segment(aes(x = recall_min, xend = recall_max, yend = qps), linewidth = 0.4,
                 alpha = 0.45, show.legend = FALSE) +
    geom_path(data = paths(single, c("scenario", "series_key")), linewidth = 0.8) +
    geom_point(size = 2.7, show.legend = TRUE) +
    scale_linetype_manual(values = single_lines, limits = single_keys, labels = single_labels[single_keys]) +
    scale_x_continuous(limits = c(0, 1), breaks = seq(0, 1, 0.2), expand = expansion(mult = 0)) +
    labs(x = "Recall@10")
  single_plot <- common_plot(single_plot, single, single_annotations,
                            "SIFT1B: single-thread recall-QPS comparison",
                            if (refreshed_spann)
                              "1 query thread | 1000 fixed queries | SPANN remeasured; DiskANN/PipeANN retained"
                            else if (supplemented)
                              "1 query thread | 1000 fixed queries | original curves plus registered unfiltered-only supplement"
                            else
                              "1 query thread | 1000 fixed queries | historical SPANN/PipeANN baselines, not a fresh paired rerun",
                            captions$single_thread_recall_qps, engine_labels = single_labels,
                            legend_keys = single_keys, plot_colors = single_colors, plot_shapes = single_shapes)
  plots <- list(single_thread_recall_qps = single_plot)
  if (stage == "complete") {
    scaling_plot <- ggplot(throughput, aes(threads, qps, colour = engine, shape = engine,
                                           linetype = engine, group = interaction(engine, recall_target, L))) +
      geom_vline(xintercept = declaration$throughput_core_budget, colour = "grey55",
                 linetype = "dotted", linewidth = 0.5) +
      geom_linerange(aes(ymin = qps_min, ymax = qps_max), linewidth = 0.4,
                     alpha = 0.45, show.legend = FALSE) +
      geom_path(data = paths(throughput, c("scenario", "engine", "recall_target", "L")), linewidth = 0.8) +
      geom_point(size = 2.7, show.legend = TRUE) +
      scale_linetype_manual(values = line_types, limits = engines, labels = throughput_labels[engines]) +
      scale_x_continuous(trans = "log2", limits = range(thread_grid),
                         breaks = c(1, 4, 16, 48, 96, 192)) +
      labs(x = "Query threads (log2 scale)")
    plots$throughput_scaling <- common_plot(
      scaling_plot, throughput, throughput_annotations,
      "SIFT1B: observed matched-recall throughput scaling",
      "Recall minimum >= 0.90 / 0.95 (primary) | each repetition >= 30 seconds | 96-core budget; OS/AIO settings retained",
      captions$throughput_scaling, target_facets = TRUE, engine_labels = throughput_labels)
    peak_plot <- ggplot(peaks, aes(engine, qps, colour = engine, shape = engine)) +
      geom_linerange(aes(ymin = qps_min, ymax = qps_max), linewidth = 0.5,
                     alpha = 0.6, show.legend = FALSE) +
      geom_point(size = 3.2, show.legend = TRUE) +
      geom_text(aes(label = point_label), vjust = -1.1, size = 2.7, lineheight = 0.95,
                show.legend = FALSE) +
      scale_x_discrete(limits = engines, drop = FALSE,
                       labels = c("SPTAG\nadaptive",
                                  if (is.null(declaration$native_pipeann_correctness)) "PipeANN"
                                  else "PipeANN\n(corrected)",
                                  "Filtered-\nDiskANN")) +
      labs(x = NULL)
    plots$throughput_peak <- common_plot(
      peak_plot, peaks, throughput_annotations,
      "SIFT1B: maximum observed median throughput",
      "Separate R90/R95 matched-recall views | resource-limited observations, not an unconstrained algorithm maximum",
      captions$throughput_peak, target_facets = TRUE, engine_labels = throughput_labels)
  }

  check_inputs()
  if (exists(output)) fail("Refusing to overwrite output; choose a new output directory")
  if (!dir.create(output, recursive = TRUE, showWarnings = FALSE)) fail("Cannot create new output directory")
  figure_files <- as.vector(outer(names(plots), c("png", "pdf"), paste, sep = "."))
  files <- c(figure_files, if (stage == "complete") "peak_points.csv", "plot_metadata.json")
  success <- FALSE
  on.exit({
    if (!success) {
      unlink(file.path(output, files))
      if (length(list.files(output, all.files = TRUE, no.. = TRUE)) == 0L) unlink(output, recursive = TRUE)
    }
  }, add = TRUE)
  for (stem in names(plots)) {
    height <- (if (stem == "single_thread_recall_qps") 6.6 else 11.4) +
      0.16 * length(strsplit(captions[[stem]], "\n")[[1]])
    for (extension in c("png", "pdf")) {
      ggsave(file.path(output, paste0(stem, ".", extension)), plot = plots[[stem]],
             width = 22, height = height, dpi = 180, bg = "white",
             device = if (extension == "pdf") grDevices::cairo_pdf else "png")
    }
  }
  if (stage == "complete") write.csv(peak_raw, file.path(output, "peak_points.csv"), row.names = FALSE)
  check_inputs()
  hashed_outputs <- setdiff(files, "plot_metadata.json")
  metadata <- list(
    schema_version = 1L, status = "completed", stage = stage, dataset = declaration$dataset,
    registration = declaration, scenarios = I(scenarios), engine_order = I(engines),
    engine_labels = as.list(labels), engine_colors = as.list(colors),
    single_series_labels = as.list(single_labels),
    single_line_groups = "Scenario, engine and measurement_series; never join historical/current clients",
    throughput_engine_labels = as.list(throughput_labels),
    scenario_metadata = scenario_metadata,
    hash_algorithm = "md5", input_files = as.list(inputs), input_hashes = as.list(input_hashes),
    output_hashes = as.list(setNames(hash_files(file.path(output, hashed_outputs)), hashed_outputs)),
    single_points = nrow(single), throughput_points = if (stage == "complete") nrow(throughput) else NULL,
    peak_points = if (stage == "complete") nrow(peaks) else NULL,
    all_measured_points_retained = TRUE, interpolated = FALSE, smoothed = FALSE, extrapolated = FALSE,
    single_point_order = "Registered scenario, engine, ascending native L; never recall order",
    throughput_line_groups = "Scenario, recall_target, engine and native L; ascending measured threads",
    throughput_facet_variables = I(c("scenario", "recall_target")),
    throughput_facet_count = if (stage == "complete") length(scenarios) * length(recall_targets) else NULL,
    throughput_unique_measurement_records = if (stage == "complete")
      nrow(unique(throughput[setdiff(throughput_required, "recall_target")])) else NULL,
    recall_target_views = list(primary = declaration$recall_target, targets = I(recall_targets),
                               labels = as.list(target_labels), independent_repeats = FALSE,
                               policy = "Identical measurements across targets are labeled views, never pooled as independent repeats"),
    recall_axis = c(0, 1), qps_axis = "log10",
    historical_reuse = list(engines = I(if (refreshed_spann) engines[2:3] else engines[1:2]),
                            source = if (refreshed_spann) spann_refresh$previous_directory else "Sep24",
                            fresh_paired_rerun = FALSE, measurements_modified = FALSE),
    spann_single_refresh = spann_refresh,
    native_io = list(SPTAG_adaptive = "buffered", PipeANN = "direct", Filtered_DiskANN = "direct"),
    matched_io_comparison = FALSE, fixed_query_working_set = 1000L, global_page_cache_drops = FALSE,
    availability = availability,
    availability_scope = "Exceptions-only logs are valid; explicit available records are optional. Unavailable recall_target/threads scopes are authoritative, blanks are wildcards, and missing target series are allowed.",
    facet_annotations = list(single = as.list(single_annotations),
                             throughput = if (stage == "complete") lapply(throughput_annotations, as.list) else NULL),
    peak_rule = "Maximum observed median QPS per scenario/engine/recall_target at validated recall/duration; ties: fewer threads, then smaller native L",
    peak_export = "Original throughput CSV measurement strings unchanged; derived boundary/resource fields added",
    largest_tested_threads_definition = "Largest successfully measured thread count per scenario/engine/recall_target; excludes blocked attempts",
    boundary_censored_definition = "Any tied maximum reaches largest_tested_threads; selected peak may use fewer threads",
    saturation_proven = FALSE, system_settings_changed = FALSE, captions = captions,
    r_version = R.version.string, ggplot2_version = as.character(packageVersion("ggplot2")),
    jsonlite_version = as.character(packageVersion("jsonlite")))
  write_json(metadata, file.path(output, "plot_metadata.json"), auto_unbox = TRUE,
             pretty = TRUE, digits = NA, dataframe = "rows", null = "null")
  check_inputs()
  success <- TRUE
  cat("Created", stage, "three-way figures with", nrow(single), "unaltered single-thread points",
      if (stage == "complete") paste("and", nrow(throughput), "validated throughput points") else "",
      "in", output, "\n")
}

render_threeway(args[[1]], args[[2]], args[[4]])

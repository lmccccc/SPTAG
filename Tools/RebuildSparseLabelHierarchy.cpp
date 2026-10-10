// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/Index.h"
#include "inc/Helper/BuildProgress.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sys/resource.h>

using namespace SPTAG;
namespace fs = std::filesystem;
using Hierarchy = SPANN::SparseLabelHierarchy;

static std::string Lower(std::string text)
{
    std::transform(text.begin(), text.end(), text.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}
static bool Within(const fs::path& child, const fs::path& parent)
{
    auto at = child.begin();
    for (const auto& part : parent)
        if (at == child.end() || *at++ != part) return false;
    return true;
}
static void LinkUnchanged(const fs::path& source, const fs::path& target,
                          const std::set<fs::path>& replaced, const fs::path& relative = {})
{
    Hierarchy::Require(fs::create_directory(target), "Cannot exclusively create destination directory");
    for (const auto& entry : fs::directory_iterator(source)) {
        const auto child = relative / entry.path().filename();
        if (replaced.count(child)) continue;
        if (entry.is_directory()) LinkUnchanged(entry.path(), target / entry.path().filename(), replaced, child);
        else {
            Hierarchy::Require(entry.is_regular_file(), "Source contains a nonregular artifact");
            fs::create_symlink(fs::canonical(entry.path()), target / entry.path().filename());
        }
    }
}

static void WriteSizing(std::ostream& report, const fs::path& path)
{
    std::ifstream input(path, std::ios::binary);
    Hierarchy::Header header;
    SPANN::SampledHierarchySizing sizing;
    input.read(reinterpret_cast<char*>(&header), sizeof(header));
    input.seekg(-static_cast<std::streamoff>(sizeof(sizing)), std::ios::end);
    input.read(reinterpret_cast<char*>(&sizing), sizeof(sizing));
    Hierarchy::Require(bool(input) && header.version == 6, "Cannot read authenticated sizing report");
    sizing.Validate(header.caps, header.replicas);
    report << ",\n  \"sizing\": {\"estimator_version\": " << sizing.version
           << ", \"target_label_row_size\": " << sizing.target
           << ", \"sample_head_limit\": " << sizing.sampleHeads << ", \"tiers\": [";
    for (std::size_t level = 0; level < sizing.tiers.size(); ++level) {
        const auto& tier = sizing.tiers[level];
        if (level) report << ',';
        report << "\n    {\"tier\": " << level + 2 << ", \"physical_children\": " << tier.children
               << ", \"logical_child_labels\": " << tier.pairs << ", \"parents\": " << tier.parents
               << ", \"label_rows\": " << tier.rows << ", \"references\": " << tier.references
               << ", \"mean_label_row\": " << (tier.rows ? double(tier.references) / tier.rows : 0)
               << ", \"ratio\": " << (tier.children ? double(tier.parents) / tier.children : 0)
               << ", \"trials\": [";
        for (std::size_t pass = 0; pass < tier.trials; ++pass) {
            const auto& trial = tier.pilot[pass];
            if (pass) report << ',';
            report << "{\"heads\": " << trial.heads << ", \"pairs\": " << trial.pairs
                   << ", \"parents\": " << trial.parents << ", \"rows\": " << trial.rows
                   << ", \"references\": " << trial.references << ", \"sample_hash\": " << trial.sampleHash
                   << ", \"estimated_references\": " << trial.estimatedReferences << '}';
        }
        report << "]}";
    }
    report << "\n  ]}";
}

int main(int argc, char** argv)
{
    if (argc != 3 || std::string(argv[1]) != "-c") {
        std::cerr << "Usage: rebuildsparselabelhierarchy -c NATIVE_INI\n";
        return 2;
    }
    const auto begin = std::chrono::steady_clock::now();
    try {
        Helper::IniReader config;
        Hierarchy::Require(config.LoadIniFile(argv[2]) == ErrorCode::Success, "Cannot read native rebuild INI");
        const fs::path sourceName = config.GetParameter("RebuildHierarchy", "SourceIndex", std::string());
        const fs::path targetName = config.GetParameter("RebuildHierarchy", "OutputIndex", std::string());
        Hierarchy::Require(sourceName.is_absolute() && targetName.is_absolute(), "Index paths must be absolute");
        const auto source = fs::canonical(sourceName);
        const auto target = fs::weakly_canonical(targetName);
        Hierarchy::Require(!fs::exists(target) && !fs::is_symlink(targetName) &&
            fs::is_directory(target.parent_path()) && !Within(source, target) && !Within(target, source),
            "Output must be new, nonoverlapping, with an existing parent");
        Helper::IniReader original;
        Hierarchy::Require(original.LoadIniFile((source / "indexloader.ini").string()) == ErrorCode::Success,
            "Cannot read source index INI");
        const auto safeName = [](const std::string& text) {
            Hierarchy::Require(!text.empty() && fs::path(text).filename() == fs::path(text) &&
                text != "." && text != "..", "Unsafe native artifact name");
            return text;
        };
        const auto vectors = safeName(original.GetParameter("SelectHead", "HierarchyHeadVectors",
            std::string("SPTAGSecondLevelHeadVectors.bin")));
        const auto ids = safeName(original.GetParameter("SelectHead", "HierarchyHeadVectorIDs",
            std::string("SPTAGSecondLevelHeadVectorIDs.bin")));
        const auto postings = safeName(original.GetParameter("SelectHead", "HierarchyPostingFile",
            std::string("second_level_head_postings.bin")));
        Hierarchy::Require(original.GetParameter<int>("SelectHead", "HierarchyLevels", 0) == 5 &&
            original.GetParameter("SelectHead", "HierarchyLabelSelectivity", std::string()).empty() &&
            original.GetParameter<int>("SelectHead", "HierarchyLocalTarget", 0) == 0 &&
            original.GetParameter<int>("SelectHead", "HierarchyLocalWindow", 0) == 0 &&
            original.GetParameter<int>("SelectHead", "HierarchyTargetPostingSize", 0) == 0 &&
            original.GetParameter<int>("SelectHead", "HierarchySizingSampleHeads", 0) == 0,
            "Source must be the canonical five-level spatial hierarchy");
        SPANN::Options settings;
        for (const auto& parameter : config.GetParameters("SelectHead")) {
            const auto key = Lower(parameter.first);
            Hierarchy::Require(key == "hierarchylabelselectivity" || key == "numberofthreads" ||
                key == "hierarchylocaltarget" || key == "hierarchylocalwindow" ||
                key == "hierarchytargetpostingsize" || key == "hierarchysizingsampleheads",
                "Rebuild INI may change only admission, sampled upper sizing and native construction threads");
            Hierarchy::Require(settings.SetParameter("SelectHead", parameter.first.c_str(), parameter.second.c_str()) ==
                ErrorCode::Success, "Invalid native reconstruction parameter");
        }
        settings.m_ratio = original.GetParameter<double>("SelectHead", "Ratio", 0);
        Hierarchy::AdmissionParameters(settings);
        Hierarchy::Require(settings.m_iSelectHeadNumberOfThreads > 0, "Invalid native construction thread count");
        std::shared_ptr<VectorIndex> index;
        Helper::BuildProgress loadProgress("source-load");
        Hierarchy::Require(VectorIndex::LoadIndex(source.string(), index) == ErrorCode::Success,
            "Cannot authenticate source SPANN index");
        loadProgress.Finish();
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
        Hierarchy::Require(spann != nullptr, "Source is not SPANN");
        auto build = *spann->GetOptions();
        build.m_hierarchyLabelSelectivity = settings.m_hierarchyLabelSelectivity;
        build.m_hierarchyLocalTarget = settings.m_hierarchyLocalTarget;
        build.m_hierarchyLocalWindow = settings.m_hierarchyLocalWindow;
        build.m_hierarchyTargetPostingSize = settings.m_hierarchyTargetPostingSize;
        build.m_hierarchySizingSampleHeads = settings.m_hierarchySizingSampleHeads;
        build.m_iSelectHeadNumberOfThreads = settings.m_iSelectHeadNumberOfThreads;
        const auto loaded = std::chrono::steady_clock::now();
        const std::string outputPosting = "sparse_label_hierarchy.bin";
        std::set<fs::path> replaced = {"indexloader.ini", outputPosting,
            "sparse-hierarchy-completion.json", "rebuild-hierarchy.ini",
            safeName(original.GetParameter("SelectHead", "HierarchyHeadIndexFolder",
                std::string("SecondLevelHeadIndex")))};
        for (int layer = 1; layer <= 4; ++layer) {
            const auto suffix = layer == 1 ? "" : ".level" + std::to_string(layer);
            replaced.insert(vectors + suffix);
            replaced.insert(vectors + suffix + ".owned");
            replaced.insert(ids + suffix);
            replaced.insert(postings + suffix);
        }
        LinkUnchanged(source, target, replaced);
        fs::copy_file(fs::canonical(argv[2]), target / "rebuild-hierarchy.ini");
        Helper::BuildProgress rebuildProgress("upper-reconstruction");
        Hierarchy::Require(spann->RebuildSparseHierarchy(build, (target / outputPosting).string()) == ErrorCode::Success,
            "Native sparse hierarchy build/reload failed");
        rebuildProgress.Finish();
        {
            std::ifstream input(source / "indexloader.ini");
            std::ofstream output(target / "indexloader.ini");
            std::string line, section;
            int rebound = 0, declared = 0;
            while (std::getline(input, line)) {
                if (!line.empty() && line.front() == '[' && line.back() == ']') {
                    section = Lower(line.substr(1, line.size() - 2));
                    output << line << '\n';
                    if (section == "selecthead") {
                        output << "HierarchyLabelSelectivity=" << build.m_hierarchyLabelSelectivity
                               << "\nHierarchyLocalTarget=" << build.m_hierarchyLocalTarget
                               << "\nHierarchyLocalWindow=" << build.m_hierarchyLocalWindow
                               << "\nHierarchyTargetPostingSize=" << build.m_hierarchyTargetPostingSize
                               << "\nHierarchySizingSampleHeads=" << build.m_hierarchySizingSampleHeads
                               << "\nHierarchyPostingFile=" << outputPosting
                               << "\nHierarchyGenerationFingerprint=\nNumberOfThreads="
                               << build.m_iSelectHeadNumberOfThreads << '\n';
                        ++declared;
                    }
                    continue;
                }
                const auto equals = line.find('=');
                const auto key = equals == std::string::npos ? "" : Lower(line.substr(0, equals));
                if (section == "selecthead" && (key == "hierarchylabelselectivity" || key == "hierarchypostingfile" ||
                    key == "hierarchygenerationfingerprint" || key == "numberofthreads" ||
                    key == "hierarchylocaltarget" || key == "hierarchylocalwindow" ||
                    key == "hierarchytargetpostingsize" || key == "hierarchysizingsampleheads")) continue;
                if (section == "base" && key == "indexdirectory") {
                    output << "IndexDirectory=" << target.string() << '\n';
                    ++rebound;
                } else output << line << '\n';
            }
            output.close();
            Hierarchy::Require(input.eof() && output && rebound == 1 && declared == 1,
                "Cannot publish the sparse index native INI");
        }
        rusage usage{};
        Hierarchy::Require(getrusage(RUSAGE_SELF, &usage) == 0, "Cannot measure native reconstruction RSS");
        std::ofstream report(target / "sparse-hierarchy-completion.json");
        report << std::setprecision(17);
        report << "{\n  \"format\": \"sparse-label-hierarchy-v"
               << (build.m_hierarchyTargetPostingSize ? 6 : build.m_hierarchyLocalTarget ? 5 : 4) << "\","
               << "\n  \"assignment\": \"adjacent-limited-label-native-h-posting-rng\",\n  \"source\": " << std::quoted(source.string())
               << ",\n  \"head_selection\": \"population-prioritized-native-bkt-over-unique-eligible-children\""
               << ",\n  \"construction_search\": \"bounded-native-h-placement-v1\""
               << ",\n  \"label_selection\": \"anchor-plus-nearest-candidate-prefix-with-repeated-labels\""
               << ",\n  \"head_count\": " << std::quoted(build.m_hierarchyTargetPostingSize ?
                   "sampled-native-O-H-label-row-sizing-v1-with-source-tier-caps" :
                   "per-level-physical-child-ratio-with-source-tier-caps")
               << ",\n  \"replicas\": \"native-H-upper-limit-no-fill; selected-anchor-is-own-member\""
               << ",\n  \"entries\": \"adjacent-reverse-owners-plus-H2-spatial-fallback\""
               << ",\n  \"base_label_slots\": " << build.m_limitedTagSlotsPerHead
               << ",\n  \"support_floor\": " << build.m_limitedTagMinHeadCount
               << ",\n  \"native_support_expansion\": " << (build.m_enableLimitedTagSupportExpansion ? "true" : "false")
               << ",\n  \"destination\": " << std::quoted(target.string())
               << ",\n  \"selectivity\": " << std::quoted(build.m_hierarchyLabelSelectivity)
               << ",\n  \"local_target\": " << build.m_hierarchyLocalTarget
               << ",\n  \"local_base_window\": " << build.m_hierarchyLocalWindow
               << ",\n  \"h1_and_ssd\": \"unchanged canonical symlinks\",\n  \"old_upper_catalogs\": \"not retained\""
               << ",\n  \"posting_bytes\": " << fs::file_size(target / outputPosting)
               << ",\n  \"source_load_seconds\": " << std::chrono::duration<double>(loaded - begin).count()
               << ",\n  \"build_seconds\": " << std::chrono::duration<double>(std::chrono::steady_clock::now() - loaded).count()
               << ",\n  \"peak_rss_kib\": " << usage.ru_maxrss;
        if (build.m_hierarchyTargetPostingSize) WriteSizing(report, target / outputPosting);
        report << "\n}\n";
        report.close();
        Hierarchy::Require(bool(report), "Cannot persist sparse hierarchy completion");
        std::cout << "Upper-only sparse hierarchy created at " << target << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Sparse hierarchy reconstruction failed: " << error.what()
                  << "\nPartial output is retained; source files were not opened for writing.\n";
        return 1;
    }
}

// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/CanonicalHierarchyVectors.h"
#include <chrono>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <set>
#include <sys/resource.h>

using namespace SPTAG;
namespace fs = std::filesystem;
static void Require(bool ok, const std::string& reason)
{
    if (!ok) throw std::runtime_error(reason);
}

static std::vector<std::uint64_t> ReadIDs(const fs::path& path)
{
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    Require(bool(in), "Cannot open native IDs: " + path.string());
    const auto bytes = in.tellg();
    in.seekg(0);
    std::int32_t count = 0, dimension = 0;
    in.read(reinterpret_cast<char*>(&count), sizeof(count));
    in.read(reinterpret_cast<char*>(&dimension), sizeof(dimension));
    Require(in && count > 0 && dimension == 1 &&
        static_cast<std::uint64_t>(bytes) == 8 + static_cast<std::uint64_t>(count) * 8,
        "Invalid native ID header: " + path.string());
    std::vector<std::uint64_t> ids(count);
    in.read(reinterpret_cast<char*>(ids.data()), ids.size() * sizeof(ids[0]));
    Require(bool(in), "Truncated native IDs");
    return ids;
}

static void LinkUnchanged(const fs::path& source, const fs::path& target,
                          const std::set<fs::path>& replaced, const fs::path& relative = {})
{
    fs::create_directory(target);
    for (const auto& entry : fs::directory_iterator(source)) {
        const auto child = relative / entry.path().filename();
        if (replaced.count(child)) continue;
        if (entry.is_directory())
            LinkUnchanged(entry.path(), target / entry.path().filename(), replaced, child);
        else {
            Require(entry.is_regular_file(), "Nonregular source index artifact");
            fs::create_symlink(fs::canonical(entry.path()), target / entry.path().filename());
        }
    }
}

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::cerr << "Usage: compactspannindex SOURCE_TENANT NEW_TENANT\n";
        return 2;
    }
    const auto begin = std::chrono::steady_clock::now();
    try {
        const auto source = fs::canonical(argv[1]);
        const auto target = fs::absolute(argv[2]).lexically_normal();
        Require(!fs::exists(target), "Destination must not exist");
        Require(fs::is_directory(target.parent_path()), "Destination parent must already exist");
        Helper::IniReader ini;
        Require(ini.LoadIniFile((source / "indexloader.ini").string()) == ErrorCode::Success,
            "Cannot read source native INI");
        const auto safeName = [](const std::string& name) {
            Require(!name.empty() && fs::path(name).filename() == fs::path(name) &&
                    name != "." && name != "..", "Unsafe native artifact name");
            return name;
        };
        const auto headFolder = safeName(ini.GetParameter("Base", "HeadIndexFolder", std::string("HeadIndex")));
        Require(!fs::exists(source / headFolder / "head_metaonly.bin"),
            "Canonical conversion requires the existing full H1 graph, not a graphless store");
        const auto schema = TagSchema::Parse(
            ini.GetParameter("BuildSSDIndex", "ColumnTypes", std::string()));
        const auto generation = ini.GetParameter<std::uint64_t>(
            "BuildSSDIndex", "LimitedTagGenerationFingerprint", 0);
        Require(generation != 0, "Missing authenticated support generation");
        const auto headIDs = ReadIDs(source / safeName(
            ini.GetParameter("Base", "HeadVectorIDs", std::string("SPTAGHeadVectorIDs.bin"))));
        std::shared_ptr<VectorIndex> heads;
        Require(VectorIndex::LoadIndex((source / headFolder).string(), heads) == ErrorCode::Success &&
            heads && heads->GetIndexAlgoType() == IndexAlgoType::BKT &&
            heads->GetNumSamples() == static_cast<SizeType>(headIDs.size()) &&
            !heads->m_pQuantizer, "Cannot load unchanged unquantized native H1 graph");
        const auto loaded = std::chrono::steady_clock::now();
        Require(SPANN::LoadHeadNodeMetadata((source / headFolder / "head_node_meta.bin").string(),
            heads, heads->GetNumSamples(), generation,
            [&](SizeType head) {
                return headIDs[head] < static_cast<std::uint64_t>(MaxSize)
                    ? static_cast<SizeType>(headIDs[head]) : MaxSize;
            }, &schema), "Cannot authenticate/convert head metadata");
        const int levels = ini.GetParameter<int>("SelectHead", "HierarchyLevels", 1);
        Require(levels >= 2 && levels <= 5, "Expected existing H2..H5 hierarchy");
        const auto vectors = safeName(ini.GetParameter("SelectHead", "HierarchyHeadVectors",
            std::string("SPTAGSecondLevelHeadVectors.bin")));
        const auto idsName = safeName(ini.GetParameter("SelectHead", "HierarchyHeadVectorIDs",
            std::string("SPTAGSecondLevelHeadVectorIDs.bin")));
        const auto name = [](const std::string& base, int layer) {
            return base + (layer == 1 ? "" : ".level" + std::to_string(layer));
        };
        const auto selectionVectors = safeName(ini.GetParameter("Base", "HeadVectors",
            std::string("SPTAGHeadVectors.bin")));
        // The full native H1 graph owns its vectors; the selection checkpoint is
        // not a runtime catalog and is not a dependency of the reconstructed view.
        std::set<fs::path> replaced = {"indexloader.ini", fs::path(headFolder) / "head_node_meta.bin",
            selectionVectors, selectionVectors + ".owned",
            safeName(ini.GetParameter("SelectHead", "HierarchyHeadIndexFolder",
                std::string("SecondLevelHeadIndex")))};
        for (int layer = 1; layer < levels; ++layer) {
            replaced.insert(name(vectors, layer));
            replaced.insert(name(vectors, layer) + ".owned");
        }
        LinkUnchanged(source, target, replaced);
        {
            std::ifstream input(source / "indexloader.ini");
            std::ofstream output(target / "indexloader.ini");
            std::string line;
            int changed = 0;
            while (std::getline(input, line)) {
                if (line.rfind("IndexDirectory=", 0) == 0) {
                    line = "IndexDirectory=" + target.string();
                    ++changed;
                }
                output << line << '\n';
            }
            output.close();
            Require(input.eof() && output && changed == 1, "Cannot rebind native destination directory");
        }
        Require(SPANN::SaveHeadNodeMetadata((target / headFolder / "head_node_meta.bin").string(),
            heads, generation), "Cannot save native compact metadata");
        std::vector<std::uint32_t> previous;
        std::uint64_t oldVectorBytes = 0, mappingBytes = 0;
        for (int layer = 1; layer < levels; ++layer) {
            const auto lower = ReadIDs(source / name(idsName, layer));
            std::vector<std::uint32_t> physical;
            Require(SPANN::FlattenHierarchyIDs(lower, heads->GetNumSamples(), previous, physical),
                "Invalid hierarchy physical mapping");
            std::shared_ptr<VectorSet> original;
            std::string error;
            Require(SPANN::LoadCanonicalOrOwnedHierarchyVectors((source / name(vectors, layer)).string(),
                heads->GetVectorValueType(), heads->GetFeatureDim(), static_cast<SizeType>(physical.size()),
                heads, original, &error, &physical) == ErrorCode::Success, error);
            Require(SPANN::SaveCanonicalHierarchyVectors((target / name(vectors, layer)).string(),
                heads, physical, *original), "Hierarchy vectors are not exact H1 copies");
            oldVectorBytes += fs::file_size(source / name(vectors, layer));
            mappingBytes += physical.size() * sizeof(physical[0]);
            previous = std::move(physical);
        }
        const auto end = std::chrono::steady_clock::now();
        rusage usage{};
        Require(getrusage(RUSAGE_SELF, &usage) == 0, "Cannot measure reconstruction RSS");
        std::ofstream report(target / "compact-conversion.json");
        report << "{\n  \"metadata_version\": 9,\n  \"schema\": " << std::quoted(schema.text)
               << ",\n  \"head_count\": " << heads->GetNumSamples()
               << ",\n  \"head_stride\": " << heads->GetHeadNodeMetaStride()
               << ",\n  \"head_metadata_payload\": " << heads->GetHeadNodeMetaBlob().size()
               << ",\n  \"upper_source_persisted_bytes\": " << oldVectorBytes
               << ",\n  \"upper_mapping_payload\": " << mappingBytes
               << ",\n  \"upper_owned_vector_bytes\": 0"
               << ",\n  \"metadata_input_chunk_rows\": 4096"
               << ",\n  \"head_load_seconds\": " << std::chrono::duration<double>(loaded - begin).count()
               << ",\n  \"reconstruction_seconds\": " << std::chrono::duration<double>(end - loaded).count()
               << ",\n  \"peak_rss_kib\": " << usage.ru_maxrss << "\n}\n";
        report.close();
        Require(bool(report), "Cannot save reconstruction accounting");
        std::cout << "Reconstructed native compact tenant at " << target << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Compact reconstruction failed: " << error.what()
                  << "\nAny partial destination is retained for inspection; source was not modified.\n";
        return 1;
    }
}

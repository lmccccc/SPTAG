// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/SPANN/Index.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace
{
struct TagCoverage
{
    std::uint64_t vectors = 0;
    std::uint64_t ownHeads = 0;
    std::uint64_t baseHeads = 0;
    std::uint64_t extraHeads = 0;
    std::uint64_t supportHeads = 0;
    std::uint64_t requiredHeads = 0;
    std::uint64_t hPostings = 0;
    std::uint64_t oPostings = 0;
    std::uint64_t effectiveHeads = 0;
    std::uint64_t hRecords = 0;
    std::uint64_t oRecords = 0;
    std::uint64_t extraHPostings = 0;
    std::uint64_t observedVectors = 0;
};

void HashU32(std::uint64_t& hash, std::uint32_t value)
{
    for (int byte = 0; byte < 4; ++byte)
    {
        hash ^= (value >> (byte * 8)) & 0xffU;
        hash *= 1099511628211ULL;
    }
}

int Fail(const std::string& message)
{
    std::cerr << message << '\n';
    return 1;
}
}

int main(int argc, char** argv)
{
    using namespace SPTAG;
    if (argc != 3)
    {
        std::cerr << "usage: spannsupportaudit <native-tenant-index> <output-prefix>\n";
        return 2;
    }
    std::shared_ptr<VectorIndex> index;
    if (VectorIndex::LoadIndex(argv[1], index) != ErrorCode::Success || !index)
        return Fail("Cannot load native SPANN index.");
    auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
    if (spann == nullptr || spann->GetDiskIndex() == nullptr)
        return Fail("Expected native SPANN postings.");
    const auto* options = spann->GetOptions();
    const std::size_t valueBytes = GetValueTypeSize(index->GetVectorValueType());
    if (!options->m_enableLimitedTagPosting || options->m_storage != Storage::STATIC ||
        !options->m_excludehead || options->m_enableDataCompression ||
        options->m_enableDeltaEncoding || options->m_enablePostingListRearrange ||
        valueBytes == 0 ||
        options->m_numTagsPerVec <= 0 || options->m_limitedTagColumn < 0 ||
        options->m_limitedTagColumn >= options->m_numTagsPerVec)
        return Fail("Audit requires raw, immutable limited-tag H|O postings with ExcludeHead and a supported value type.");

    const auto disk = spann->GetDiskIndex();
    const SizeType documents = index->GetNumSamples();
    const SizeType heads = disk->GetPostingCount();
    if (documents <= 0 || heads <= 0 || heads > documents)
        return Fail("Invalid native document/head counts.");
    std::uint64_t generation = 0;
    if (!Helper::Convert::ConvertStringTo<std::uint64_t>(
            options->m_limitedTagGenerationFingerprint.c_str(), generation))
        return Fail("Invalid native limited-tag generation.");
    const auto supportPath = std::filesystem::path(argv[1]) / options->m_limitedTagSupportFile;
    SPANN::LimitedTagSupport support;
    std::string error;
    if (!support.Load(supportPath.string(), heads, options->m_limitedTagSlotsPerHead,
            options->m_limitedTagMinHeadCount,
            options->m_limitedTagColumn, options->m_numTagsPerVec, generation, &error))
        return Fail("Cannot load support metadata: " + error);

    const auto prefix = std::filesystem::path(argv[2]);
    if (!prefix.parent_path().empty()) std::filesystem::create_directories(prefix.parent_path());
    std::ofstream postings(prefix.string() + ".postings.csv");
    if (!postings) return Fail("Cannot create posting coverage CSV.");
    postings << "head,own_tag,base_supports,extra_supports,h_records,o_records,h_payload_pages,h_beyond_cut_records,h_overflow_pages\n";
    std::map<std::uint32_t, TagCoverage> coverage;
    for (const auto& entry : support.TagHeads())
    {
        auto& row = coverage[entry.first];
        row.vectors = support.TagVectorCount(entry.first);
        row.supportHeads = entry.second.size();
        row.requiredHeads = support.RequiredHeadCount(entry.first);
    }
    std::vector<std::uint8_t> isHead(static_cast<size_t>(documents), 0);
    std::vector<std::uint8_t> hSeen(static_cast<size_t>(documents), 0);
    std::vector<std::uint8_t> oSeen(static_cast<size_t>(documents), 0);
    std::vector<std::uint32_t> vectorTags(
        static_cast<size_t>(documents), SPANN::LimitedTagSupport::EmptyTag);
    for (SizeType head = 0; head < heads; ++head)
    {
        const auto vid = spann->GetGlobalVID(head);
        if (vid < 0 || vid >= documents || isHead[static_cast<size_t>(vid)])
            return Fail("Invalid or duplicate canonical H1 VID.");
        isHead[static_cast<size_t>(vid)] = 1;
        vectorTags[static_cast<size_t>(vid)] = support.OwnTag(head);
    }
    const std::size_t recordBytes = sizeof(SizeType) +
        static_cast<size_t>(options->m_numTagsPerVec) * sizeof(std::uint32_t) +
        static_cast<size_t>(index->GetFeatureDim()) * valueBytes;
    std::uint64_t records = 0, pureRecords = 0, headRecords = 0;
    std::uint64_t maxExpandedPurePages = 0, extraHPostings = 0;
    std::uint64_t beyondCutHRecords = 0, beyondCutHPostings = 0, hOverflowPages = 0;
    std::uint64_t originalFingerprint = 14695981039346656037ULL;
    std::uint64_t originalBytesFingerprint = 14695981039346656037ULL;
    std::uint64_t originalDistanceFingerprint = 14695981039346656037ULL;
    std::string posting;
    for (SizeType head = 0; head < heads; ++head)
    {
        const int full = disk->GetPostingVectorCount(head, false);
        const int pure = disk->GetPostingVectorCount(head, true);
        if (full < 0 || pure < 0 || pure > full)
            return Fail("Invalid H|O counts at head " + std::to_string(head));
        posting.clear();
        if (full != 0 && disk->GetWritePosting(nullptr, head, posting, false) != ErrorCode::Success)
            return Fail("Cannot read native posting " + std::to_string(head));
        if (posting.size() != static_cast<size_t>(full) * recordBytes)
            return Fail("Unexpected native posting record stride.");
        const auto own = support.OwnTag(head);
        if (coverage.find(own) == coverage.end()) return Fail("Uncounted head-owned tag.");
        ++coverage[own].ownHeads;
        ++coverage[own].effectiveHeads;
        std::size_t baseSupports = 0;
        for (int slot = 0; slot < support.SlotsPerHead(); ++slot)
        {
            const auto tag = support.TagAt(head, slot);
            if (tag == SPANN::LimitedTagSupport::EmptyTag) continue;
            ++coverage[tag].baseHeads;
            ++baseSupports;
        }
        const auto extraCount = support.ExtraTagCount(head);
        const auto* extras = support.ExtraTagData(head);
        for (size_t slot = 0; slot < extraCount; ++slot) ++coverage[extras[slot]].extraHeads;
        std::unordered_set<std::uint32_t> hTags, oTags;
        std::unordered_map<std::uint32_t, float> closestOriginalTags;
        std::unordered_set<SizeType> hVIDs, oVIDs;
        for (int row = 0; row < full; ++row)
        {
            const char* record = posting.data() + static_cast<size_t>(row) * recordBytes;
            SizeType vid = -1;
            std::uint32_t tag = SPANN::LimitedTagSupport::EmptyTag;
            std::memcpy(&vid, record, sizeof(vid));
            std::memcpy(&tag, record + sizeof(vid) +
                static_cast<size_t>(options->m_limitedTagColumn) * sizeof(tag), sizeof(tag));
            if (vid < 0 || vid >= documents || coverage.find(tag) == coverage.end())
                return Fail("Invalid posting VID or uncounted routing tag.");
            auto& known = vectorTags[static_cast<size_t>(vid)];
            if (known != SPANN::LimitedTagSupport::EmptyTag && known != tag)
                return Fail("Inconsistent routing tag for one canonical VID.");
            known = tag;
            headRecords += isHead[static_cast<size_t>(vid)];
            if (row < pure)
            {
                if (!support.Supports(head, tag) || !hVIDs.insert(vid).second)
                    return Fail("Unsupported or duplicate H assignment.");
                ++coverage[tag].hRecords;
                hTags.insert(tag);
                auto& copies = hSeen[static_cast<size_t>(vid)];
                if (copies == (std::numeric_limits<std::uint8_t>::max)() ||
                    ++copies > options->m_replicaCount)
                    return Fail("H replication exceeds the native replica limit.");
            }
            else
            {
                if (!oVIDs.insert(vid).second) return Fail("Duplicate O assignment.");
                ++coverage[tag].oRecords;
                oTags.insert(tag);
                const auto memory = spann->GetMemoryIndex();
                const char* vector = record + sizeof(SizeType) +
                    static_cast<size_t>(options->m_numTagsPerVec) * sizeof(std::uint32_t);
                const float distance = memory->ComputeDistance(memory->GetSample(head), vector);
                if (support.UsesRetainedOriginalCandidates() && tag != own)
                {
                    const auto found = closestOriginalTags.emplace(tag, distance);
                    found.first->second = (std::min)(found.first->second, distance);
                }
                auto& copies = oSeen[static_cast<size_t>(vid)];
                if (copies == (std::numeric_limits<std::uint8_t>::max)() ||
                    ++copies > options->m_replicaCount)
                    return Fail("O replication exceeds the native replica limit.");
                HashU32(originalFingerprint, static_cast<std::uint32_t>(head));
                HashU32(originalFingerprint, static_cast<std::uint32_t>(vid));
                HashU32(originalBytesFingerprint, static_cast<std::uint32_t>(head));
                for (size_t byte = 0; byte < recordBytes; ++byte) {
                    originalBytesFingerprint ^= static_cast<unsigned char>(record[byte]);
                    originalBytesFingerprint *= 1099511628211ULL;
                }
                std::uint32_t distanceBits;
                std::memcpy(&distanceBits, &distance, sizeof(distanceBits));
                HashU32(originalDistanceFingerprint, static_cast<std::uint32_t>(head));
                HashU32(originalDistanceFingerprint, static_cast<std::uint32_t>(vid));
                HashU32(originalDistanceFingerprint, distanceBits);
            }
        }
        for (const auto tag : hTags)
        {
            auto& row = coverage[tag];
            ++row.hPostings;
            if (tag != own) ++row.effectiveHeads;
            if (extraCount != 0 && std::binary_search(extras, extras + extraCount, tag))
            {
                ++row.extraHPostings;
                ++extraHPostings;
            }
        }
        for (const auto tag : oTags) ++coverage[tag].oPostings;
        if (support.UsesRetainedOriginalCandidates())
        {
            std::vector<std::pair<float, std::uint32_t>> ranked;
            for (const auto& candidate : closestOriginalTags)
                ranked.emplace_back(candidate.second, candidate.first);
            std::sort(ranked.begin(), ranked.end());
            for (int slot = 1; slot < support.SlotsPerHead(); ++slot)
            {
                const auto expected = static_cast<size_t>(slot) <= ranked.size()
                    ? ranked[slot - 1].second : SPANN::LimitedTagSupport::EmptyTag;
                if (support.TagAt(head, slot) != expected)
                    return Fail("Base support differs from nearest distinct retained O tags.");
            }
        }
        for (size_t slot = 0; slot < extraCount; ++slot)
            if (oTags.count(extras[slot]) == 0)
                return Fail("Expanded support has no retained O source.");
        const std::uint64_t purePages =
            (static_cast<std::uint64_t>(pure) * recordBytes + PageSize - 1) / PageSize;
        std::uint64_t beyondCut = 0, overflow = 0;
        if (options->m_postingPageLimit > 0)
        {
            const auto buildPages = (std::max)(
                static_cast<std::uint64_t>(options->m_postingPageLimit),
                (static_cast<std::uint64_t>((std::max)(0, options->m_postingVectorLimit)) *
                    recordBytes + PageSize - 1) / PageSize);
            const auto recordLimit = buildPages * PageSize / recordBytes;
            if (static_cast<std::uint64_t>(full - pure) > recordLimit)
                return Fail("O exceeds the native effective PostingPageLimit/PostingVectorLimit.");
            if (static_cast<std::uint64_t>(pure) > recordLimit) {
                beyondCut = static_cast<std::uint64_t>(pure) - recordLimit;
                overflow = purePages - (recordLimit * recordBytes + PageSize - 1) / PageSize;
                beyondCutHRecords += beyondCut;
                ++beyondCutHPostings;
                hOverflowPages += overflow;
            }
        }
        if (extraCount != 0)
        {
            maxExpandedPurePages = (std::max)(maxExpandedPurePages, purePages);
        }
        postings << head << ',' << own << ',' << baseSupports << ',' << extraCount << ','
                 << pure << ',' << full - pure << ',' << purePages << ','
                 << beyondCut << ',' << overflow << '\n';
        records += static_cast<std::uint64_t>(full);
        pureRecords += static_cast<std::uint64_t>(pure);
    }
    std::uint64_t uniquePure = 0, uniqueOriginal = 0, zeroHNonHeads = 0, unobservedVectors = 0;
    for (SizeType vid = 0; vid < documents; ++vid)
    {
        const auto offset = static_cast<size_t>(vid);
        zeroHNonHeads += isHead[offset] == 0 && hSeen[offset] == 0;
        const auto found = coverage.find(vectorTags[offset]);
        if (found == coverage.end()) ++unobservedVectors;
        else ++found->second.observedVectors;
        uniquePure += hSeen[offset] != 0;
        uniqueOriginal += oSeen[offset] != 0;
    }
    if (headRecords != 0) return Fail("Real H1 VID incorrectly serialized in SSD.");
    std::ofstream tags(prefix.string() + ".tags.csv");
    if (!tags) return Fail("Cannot create tag coverage CSV.");
    tags << "tag,vectors,own_heads,base_support_heads,extra_support_heads,support_heads,required_heads,"
            "h_postings,o_postings,effective_heads,h_records,o_records,extra_h_postings\n";
    std::uint64_t belowRequired = 0, belowFloor = 0, sourceCapped = 0;
    for (const auto& entry : coverage)
    {
        const auto& row = entry.second;
        if (row.observedVectors > row.vectors ||
            (unobservedVectors == 0 && row.observedVectors != row.vectors) ||
            row.supportHeads != row.baseHeads + row.extraHeads ||
            row.effectiveHeads == 0 || row.effectiveHeads > row.supportHeads)
            return Fail("Persisted tag counts or effective coverage disagree with actual records.");
        belowRequired += row.effectiveHeads < row.requiredHeads;
        belowFloor += row.effectiveHeads < static_cast<std::uint64_t>(options->m_limitedTagMinHeadCount);
        sourceCapped += row.requiredHeads < static_cast<std::uint64_t>(options->m_limitedTagMinHeadCount);
        tags << entry.first << ',' << row.vectors << ',' << row.ownHeads << ','
             << row.baseHeads << ',' << row.extraHeads << ',' << row.supportHeads << ','
             << row.requiredHeads << ',' << row.hPostings << ',' << row.oPostings << ','
             << row.effectiveHeads << ',' << row.hRecords << ',' << row.oRecords << ','
             << row.extraHPostings << '\n';
    }
    tags.close();
    postings.close();
    if (!tags || !postings) return Fail("Cannot finish coverage CSV output.");
    std::ostringstream fingerprint;
    fingerprint << std::hex << std::setfill('0') << std::setw(16) << originalFingerprint;
    std::ostringstream json;
    json << "{\"documents\":" << documents << ",\"heads\":" << heads
         << ",\"tag_count\":" << coverage.size() << ",\"records\":" << records
         << ",\"pure_records\":" << pureRecords << ",\"original_records\":" << records - pureRecords
         << ",\"payload_bytes\":" << records * recordBytes << ",\"record_bytes\":" << recordBytes
         << ",\"unique_pure_vids\":" << uniquePure << ",\"unique_original_vids\":" << uniqueOriginal
         << ",\"zero_h_nonhead_vids\":" << zeroHNonHeads
         << ",\"unobserved_vids\":" << unobservedVectors
         << ",\"beyond_cut_h_records\":" << beyondCutHRecords
         << ",\"beyond_cut_h_postings\":" << beyondCutHPostings
         << ",\"h_overflow_payload_pages\":" << hOverflowPages
         << ",\"h_overflow_provenance\":\"not_inferable_from_saved_index\""
         << ",\"head_records\":" << headRecords << ",\"extra_supports\":" << support.ExtraSupportCount()
         << ",\"support_file_bytes\":" << std::filesystem::file_size(supportPath)
         << ",\"legacy_extra_support_cap\":" << support.LegacyExtraSupportCap()
         << ",\"expansion_storage_policy\":\""
         << (!support.HasExpansion() ? "none" :
             support.LegacyExtraSupportCap() ? "legacy_v4_cap" : "v5_required_deficits") << "\""
         << ",\"below_required_tags\":" << belowRequired << ",\"below_floor_tags\":" << belowFloor
         << ",\"source_capped_tags\":" << sourceCapped << ",\"extra_h_postings\":" << extraHPostings
         << ",\"unused_extra_supports\":" << support.ExtraSupportCount() - extraHPostings
         << ",\"max_expanded_pure_pages\":" << maxExpandedPurePages
         << ",\"base_candidate_source\":\""
         << (support.UsesRetainedOriginalCandidates() ? "retained_o" : "legacy_votes") << "\""
         << ",\"original_membership_fingerprint\":\"" << fingerprint.str() << "\""
         << ",\"original_bytes_fingerprint\":\"" << std::hex << originalBytesFingerprint << "\""
         << ",\"original_distance_fingerprint\":\"" << originalDistanceFingerprint << "\"}\n";
    std::ofstream summary(prefix.string() + ".summary.json");
    summary << json.str();
    summary.close();
    if (!summary) return Fail("Cannot write coverage summary.");
    std::cout << json.str();
    return 0;
}

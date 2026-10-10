// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/PostingNavigation.h"
#include "inc/Core/SPANN/RoutingSignatures.h"
#include "inc/Core/SPANN/LocalLabelAdmission.h"
#include "inc/Core/SPANN/SampledHierarchySizing.h"
#include "inc/Core/SPANN/Options.h"
#include <array>
#include <fstream>
#include <sstream>

namespace SPTAG { namespace SPANN {

class SparseLabelHierarchy
{
public:
    static constexpr std::uint32_t None = UINT32_MAX;
    using Thresholds = std::array<double, 4>;
    struct Node {
        std::uint32_t representative = None, parent = None, tag = None, tier = 0;
        std::uint64_t begin = 0;
        std::uint32_t size = 0, reserved = 0;
    };
    static_assert(sizeof(Node) == 32, "Sparse posting node layout");
    struct LabelRow {
        std::uint32_t tag = None, size = 0;
        std::uint64_t begin = 0;
    };
    static_assert(sizeof(LabelRow) == 16, "Layered label row layout");
    struct Header {
        std::uint64_t magic = 0x315248534C4E5053ULL;
        std::uint32_t version = 3, bytes = 128;
        std::uint32_t heads = 0, nodes = 0, replicas = 0, dimension = 0;
        std::uint64_t members = 0, headIDs = 0, support = 0;
        Thresholds thresholds{};
        std::array<std::uint32_t, 4> caps{};
        std::uint64_t fingerprint = 0;
        std::uint32_t valueType = 0, assignment = 2;
        std::uint64_t reserved2 = 0;
    };
    static_assert(sizeof(Header) == 128, "Sparse posting header layout");

    static Thresholds ParseThresholds(const std::string& text)
    {
        Thresholds values{};
        std::istringstream input(text);
        std::string field;
        for (std::size_t i = 0; i < values.size(); ++i) {
            if (!std::getline(input, field, ',') || field.empty())
                throw std::invalid_argument("HierarchyLabelSelectivity requires four decreasing fractions for H2..H5");
            std::istringstream number(field);
            number >> std::noskipws >> values[i];
            if (!number || !number.eof() || !std::isfinite(values[i]) ||
                values[i] <= 0 || values[i] > 1 || (i && values[i] >= values[i - 1]))
                throw std::invalid_argument("HierarchyLabelSelectivity must satisfy 1 >= H2 > H3 > H4 > H5 > 0");
        }
        if (std::getline(input, field, ',') || (!text.empty() && text.back() == ','))
            throw std::invalid_argument("HierarchyLabelSelectivity has extra fields");
        return values;
    }
    static int Tier(const LimitedTagSupport& support, std::uint32_t tag, const Thresholds& thresholds)
    {
        const auto count = support.TagVectorCount(tag);
        if (!count || !support.VectorCount()) return 0;
        const long double selectivity = static_cast<long double>(count) / support.VectorCount();
        for (int i = 3; i >= 0; --i)
            if (selectivity <= thresholds[i]) return i + 2;
        return 0;
    }
    static void Require(bool condition, const char* message)
    {
        if (!condition) throw std::runtime_error(message);
    }
    static bool Admitted(const LimitedTagSupport& support, std::uint32_t tag,
                         const Thresholds& thresholds, int tier)
    {
        Require(tier >= 2 && tier <= 5, "Invalid label admission tier");
        return support.TagVectorCount(tag) && support.VectorCount() &&
            static_cast<long double>(support.TagVectorCount(tag)) / support.VectorCount() < thresholds[tier - 2];
    }
    static Thresholds AdmissionParameters(const Options& options)
    {
        SampledHierarchySizing::ValidateOptions(options);
        if (!options.m_hierarchyLocalTarget && !options.m_hierarchyLocalWindow)
            return ParseThresholds(options.m_hierarchyLabelSelectivity);
        if (!options.m_hierarchyLabelSelectivity.empty() || options.m_hierarchyLocalTarget <= 0 ||
            options.m_hierarchyLocalWindow <= 0 || !std::isfinite(options.m_ratio) ||
            options.m_ratio <= 0 || options.m_ratio >= 1)
            throw std::invalid_argument("Local admission requires positive HierarchyLocalTarget/Window, native Ratio, and no global thresholds");
        return {double(options.m_hierarchyLocalTarget), double(options.m_hierarchyLocalWindow), options.m_ratio, 0};
    }
    bool Local() const { return m_header.version >= 5; }
    bool Sampled() const { return m_header.version == 6; }
    const SampledHierarchySizing& Sizing() const { return m_sizing; }
    void SetSizing(const SampledHierarchySizing& sizing)
    {
        Require(Sampled() && m_ownerOffsets.empty(), "Cannot change immutable sampled sizing metadata");
        m_sizing = sizing;
    }
    void ValidateSizingOptions(const Options& options) const
    {
        SampledHierarchySizing::ValidateOptions(options);
        Require(Sampled() == bool(options.m_hierarchyTargetPostingSize) &&
            (!Sampled() || (m_sizing.target == static_cast<unsigned>(options.m_hierarchyTargetPostingSize) &&
                m_sizing.sampleHeads == static_cast<unsigned>(options.m_hierarchySizingSampleHeads))),
            "Sampled sizing settings do not match the authenticated hierarchy");
    }
    bool Layered() const { return m_header.version >= 4; }
    void SetLocalAdmission(LocalLabelAdmission policy)
    {
        Require(Local() && m_ownerOffsets.empty(), "Cannot change immutable local admission");
        policy.Validate(m_header.heads);
        m_localAdmission = std::move(policy);
    }
    bool AdmitChild(const LimitedTagSupport& support, std::uint32_t physical,
                    std::uint32_t tag, int tier) const
    {
        return Local() ? m_localAdmission.Admits(physical, tag, tier) :
            Admitted(support, tag, m_header.thresholds, tier);
    }
    bool HasQueryLabel(std::uint32_t tag) const
    {
        return std::binary_search(m_queryLabels[0].begin(), m_queryLabels[0].end(), tag);
    }
    int HighestQueryTier(const std::vector<std::uint32_t>& labels) const
    {
        Require(Layered() && !m_ownerOffsets.empty(), "Layered query label domains are not ready");
        for (int tier = 5; tier >= 2; --tier)
            for (auto tag : labels) {
                const auto& domain = m_queryLabels[tier - 2];
                if (std::binary_search(domain.begin(), domain.end(), tag)) return tier;
            }
        return 1;
    }
    void ReleaseLocalStatistics()
    {
        Require(!m_ownerOffsets.empty(), "Cannot release unvalidated construction statistics");
        if (Local()) m_localAdmission.Release();
    }

    void Initialize(const Header& header)
    {
        m_header = header;
        m_nodes.clear();
        m_members.clear();
        m_ownerOffsets.clear();
        m_ownerIDs.clear();
        m_labelRows.clear();
        m_upperOwnerOffsets.clear();
        m_upperOwnerIDs.clear();
        m_entries.assign(header.heads, -1);
        m_signatures.clear();
        m_numeric.clear();
        m_localAdmission = LocalLabelAdmission{};
        m_sizing = SampledHierarchySizing{};
        for (auto& labels : m_queryLabels) labels.clear();
    }
    std::uint32_t AddRow(std::uint32_t representative, std::uint32_t tag, int tier,
                         const std::vector<std::uint32_t>& members)
    {
        Require(!Layered(), "Layered postings require explicit label rows");
        Require(m_ownerOffsets.empty(), "Cannot change rows after sparse owner construction");
        Require(m_nodes.size() < static_cast<std::size_t>(MaxSize) &&
            !members.empty() && members.size() <= static_cast<std::size_t>(MaxSize),
            "Sparse posting row/node count exceeds native limits");
        const auto id = static_cast<std::uint32_t>(m_nodes.size());
        m_nodes.push_back({representative, None, tag, static_cast<std::uint32_t>(tier),
                          m_members.size(), static_cast<std::uint32_t>(members.size()), 0});
        m_members.insert(m_members.end(), members.begin(), members.end());
        if (tag == None)
            for (auto child : members) {
                Require(child < id && m_nodes[child].parent == None, "Sparse posting must have one acyclic owner");
                m_nodes[child].parent = id;
            }
        return id;
    }
    std::uint32_t AddLayeredRow(std::uint32_t representative, std::uint32_t anchor, int tier,
        const std::vector<std::pair<std::uint32_t, std::vector<std::uint32_t>>>& rows)
    {
        Require(Layered() && m_ownerOffsets.empty() && !rows.empty() &&
            m_nodes.size() < static_cast<std::size_t>(MaxSize) &&
            rows.size() < static_cast<std::size_t>(MaxSize) &&
            m_labelRows.size() <= static_cast<std::size_t>(MaxSize) - rows.size(),
            "Invalid layered posting construction state or label-row count");
        Node node{representative, static_cast<std::uint32_t>(m_labelRows.size()), anchor,
            static_cast<std::uint32_t>(tier), m_members.size(), 0, static_cast<std::uint32_t>(rows.size())};
        for (const auto& row : rows) {
            Require(!row.second.empty() && row.second.size() <= static_cast<std::size_t>(MaxSize) - node.size,
                "Layered posting reference count exceeds native limits");
            m_labelRows.push_back({row.first, static_cast<std::uint32_t>(row.second.size()), m_members.size()});
            m_members.insert(m_members.end(), row.second.begin(), row.second.end());
            node.size += static_cast<std::uint32_t>(row.second.size());
        }
        m_nodes.push_back(node);
        return static_cast<std::uint32_t>(m_nodes.size() - 1);
    }
    void SetEntry(SizeType head, std::uint32_t node)
    {
        Require(m_ownerOffsets.empty(), "Cannot change entries after sparse owner construction");
        m_entries.at(head) = static_cast<int>(node);
    }
    void BuildOwners()
    {
        if (m_header.version < 3) return;
        Require(m_ownerOffsets.empty(), "Sparse owners have already been constructed");
        if (Layered()) {
            for (int id = 0; id < Count(); ++id) {
                auto& labels = m_queryLabels.at(At(id).tier - 2);
                for (auto row = LabelsBegin(id); row != LabelsEnd(id); ++row) {
                    const auto at = std::lower_bound(labels.begin(), labels.end(), row->tag);
                    if (at == labels.end() || *at != row->tag) labels.insert(at, row->tag);
                }
            }
            BuildLayeredOwners();
            return;
        }
        m_ownerOffsets.assign(m_entries.size() + 1, 0);
        for (int id = 0; id < Count(); ++id)
            if (IsLeaf(id))
                for (auto p = Begin(id); p != End(id); ++p) {
                    Require(*p < m_entries.size(), "Invalid sparse reverse-owner member");
                    ++m_ownerOffsets[*p + 1];
                }
        for (std::size_t head = 0; head < m_entries.size(); ++head) {
            m_ownerOffsets[head + 1] += m_entries[head] >= 0;
            m_ownerOffsets[head + 1] += m_ownerOffsets[head];
        }
        Require(m_ownerOffsets.back() <= m_ownerIDs.max_size(), "Sparse reverse-owner count overflow");
        m_ownerIDs.resize(m_ownerOffsets.back());
        auto cursors = m_ownerOffsets;
        for (int id = 0; id < Count(); ++id)
            if (IsLeaf(id))
                for (auto p = Begin(id); p != End(id); ++p) m_ownerIDs[cursors[*p]++] = id;
        for (std::size_t head = 0; head < m_entries.size(); ++head) {
            if (m_entries[head] >= 0) m_ownerIDs[cursors[head]++] = m_entries[head];
            Require(cursors[head] == m_ownerOffsets[head + 1], "Sparse reverse-owner fill mismatch");
        }
    }
    const Header& Metadata() const { return m_header; }
    const Node& At(int id) const { return m_nodes.at(id); }
    bool IsLeaf(int id) const { return Layered() ? At(id).tier == 2 : At(id).tag != None; }
    int Parent(int id) const { return At(id).parent == None ? -1 : static_cast<int>(At(id).parent); }
    const std::uint32_t* Begin(int id) const { return m_members.data() + At(id).begin; }
    const std::uint32_t* End(int id) const { return Begin(id) + At(id).size; }
    const LabelRow* LabelsBegin(int id) const
    {
        Require(Layered(), "Label directory requires layered postings");
        return m_labelRows.data() + At(id).parent;
    }
    const LabelRow* LabelsEnd(int id) const { return LabelsBegin(id) + At(id).reserved; }
    const LabelRow* FindLabel(int id, std::uint32_t tag) const
    {
        const auto* end = LabelsEnd(id);
        const auto* row = std::lower_bound(LabelsBegin(id), end, tag,
            [](const LabelRow& value, std::uint32_t key) { return value.tag < key; });
        return row != end && row->tag == tag ? row : nullptr;
    }
    const std::uint32_t* Begin(const LabelRow& row) const { return m_members.data() + row.begin; }
    const std::uint32_t* End(const LabelRow& row) const { return Begin(row) + row.size; }
    std::pair<const std::uint32_t*, const std::uint32_t*> SelectMembers(int id,
        const std::vector<std::uint32_t>& labels, std::vector<std::uint32_t>& scratch) const
    {
        if (!Layered()) return {Begin(id), End(id)};
        scratch.clear();
        if (labels.size() == 1) {
            if (const auto* row = FindLabel(id, labels.front()))
                return {Begin(*row), End(*row)};
            return {Begin(id), Begin(id)};
        }
        for (auto tag : labels)
            if (const auto* row = FindLabel(id, tag))
                scratch.insert(scratch.end(), Begin(*row), End(*row));
        std::sort(scratch.begin(), scratch.end());
        scratch.erase(std::unique(scratch.begin(), scratch.end()), scratch.end());
        const auto* begin = scratch.empty() ? Begin(id) : scratch.data();
        return {begin, begin + scratch.size()};
    }
    template<class Visit> void VisitUpperParents(int id, const Visit& visit) const
    {
        if (!Layered()) {
            const int parent = Parent(id);
            if (parent >= 0) visit(parent);
            return;
        }
        Require(id >= 0 && id < Count() && m_upperOwnerOffsets.size() == m_nodes.size() + 1,
            "Layered upper owners are not ready");
        for (auto at = m_upperOwnerOffsets[id]; at < m_upperOwnerOffsets[id + 1]; ++at)
            visit(m_upperOwnerIDs[at]);
    }
    const SparseLabelHierarchy& operator[](std::size_t level) const
    {
        Require(level == 0, "Sparse posting storage has one physical ID space");
        return *this;
    }
    int Count(std::size_t level = 0) const
    {
        Require(level == 0, "Invalid sparse posting physical ID space");
        return static_cast<int>(m_nodes.size());
    }
    int HeadCount() const { return static_cast<int>(m_entries.size()); }
    std::size_t Levels() const { return 1; }
    PostingOwners::Range Parents(std::size_t level, int head) const
    {
        Require(level == 0 && head >= 0 && head < HeadCount(), "Invalid sparse posting anchor");
        const auto* entry = m_entries.data() + head;
        if (m_header.version >= 3) {
            Require(m_ownerOffsets.size() == m_entries.size() + 1, "Sparse reverse owners are not ready");
            if (m_ownerOffsets[head] == m_ownerOffsets[head + 1]) return {entry, entry};
            return {m_ownerIDs.data() + m_ownerOffsets[head], m_ownerIDs.data() + m_ownerOffsets[head + 1]};
        }
        return {entry, entry + (*entry >= 0 ? 1 : 0)};
    }

    void Validate(const LimitedTagSupport& support, std::uint64_t headIDs,
                  const Thresholds& thresholds, DimensionType dimension, VectorValueType type) const
    {
        Require(m_header.heads == static_cast<std::uint32_t>(support.HeadCount()) &&
            m_header.version >= 1 && m_header.version <= 6 && m_header.assignment == m_header.version - 1 &&
            m_header.heads == m_entries.size() && m_header.headIDs == headIDs &&
            m_header.support == support.ContentFingerprint() && support.HasTagVectorCounts() &&
            m_header.thresholds == thresholds && m_header.dimension == static_cast<std::uint32_t>(dimension) &&
            m_header.valueType == static_cast<std::uint32_t>(type) && m_header.replicas > 0,
            "Sparse hierarchy source/configuration binding mismatch");
        if (Layered()) {
            if (Local()) {
                Require(thresholds[0] > 0 && thresholds[0] <= MaxSize &&
                    thresholds[0] == std::floor(thresholds[0]) && thresholds[1] > 0 &&
                    thresholds[1] <= MaxSize && thresholds[1] == std::floor(thresholds[1]) &&
                    std::isfinite(thresholds[2]) && thresholds[2] > 0 && thresholds[2] < 1 &&
                    thresholds[3] == 0, "Invalid persisted local admission configuration");
                m_localAdmission.Validate(m_header.heads);
            }
            ValidateLayered(support);
            if (Sampled()) m_sizing.Validate(m_header.caps, m_header.replicas);
            return;
        }
        std::array<std::uint64_t, 4> counts{};
        std::vector<std::uint32_t> copies(m_header.heads, 0);
        std::vector<std::uint8_t> incoming(m_nodes.size(), 0);
        std::vector<std::uint32_t> labels;
        for (const auto& tag : support.TagHeads())
            if (Tier(support, tag.first, thresholds)) labels.push_back(tag.first);
        std::sort(labels.begin(), labels.end());
        std::size_t labelAt = 0, distinct = 0, labelRows = 0;
        std::uint32_t current = None, previousRepresentative = None;
        const auto finishLabel = [&]() {
            if (current == None) return;
            const auto& expected = support.TagHeads().at(current);
            Require(distinct == expected.size(), "Sparse posting omits label-supported H1 heads");
            const auto requiredCopies = std::min<std::size_t>(m_header.replicas, labelRows);
            for (int head : expected) {
                Require(m_header.version == 1 || copies[head] == requiredCopies,
                    "Label-local native assignment lost a supported-head replica");
                copies[head] = 0;
            }
        };
        std::uint64_t offset = 0;
        bool navigationStarted = false;
        for (std::size_t id = 0; id < m_nodes.size(); ++id) {
            const auto& node = m_nodes[id];
            Require(node.representative < m_header.heads && node.tier >= 2 && node.tier <= 5 &&
                node.size > 0 && node.size <= static_cast<std::uint32_t>(MaxSize) &&
                node.begin == offset && node.begin <= m_members.size() &&
                node.size <= m_members.size() - node.begin && !node.reserved &&
                (node.parent == None || (node.parent > id && node.parent < m_nodes.size())),
                "Invalid sparse posting row, representative or owner");
            ++counts[node.tier - 2];
            offset += node.size;
            if (node.tag != None) {
                Require(!navigationStarted && Tier(support, node.tag, thresholds) == static_cast<int>(node.tier),
                    "Sparse posting label is outside its selectivity tier");
                if (node.tag != current) {
                    finishLabel();
                    Require(labelAt < labels.size() && labels[labelAt++] == node.tag,
                        "Sparse posting label groups are missing, duplicated or unordered");
                    current = node.tag;
                    distinct = 0;
                    labelRows = 0;
                    previousRepresentative = None;
                }
                Require(m_header.version == 1 || previousRepresentative == None ||
                    node.representative > previousRepresentative, "Duplicate or unordered label-local representatives");
                previousRepresentative = node.representative;
                ++labelRows;
                bool containsRepresentative = false;
                std::uint32_t previous = None;
                for (auto p = Begin(id); p != End(id); ++p) {
                    Require(*p < m_header.heads && (previous == None || *p > previous) &&
                        support.Supports(*p, node.tag), "Sparse posting contains an unsupported or duplicate H1 member");
                    if (!copies[*p]++) ++distinct;
                    Require(copies[*p] <= m_header.replicas, "Sparse H1 assignment exceeds its replica cap");
                    previous = *p;
                    containsRepresentative |= *p == node.representative;
                }
                Require(containsRepresentative, "Sparse leaf representative is not one of its members");
            } else {
                navigationStarted = true;
                bool containsRepresentative = false;
                for (auto p = Begin(id); p != End(id); ++p) {
                    Require(*p < id && m_nodes[*p].parent == id && !incoming[*p]++,
                        "Invalid sparse navigation child or duplicate edge");
                    containsRepresentative |= m_nodes[*p].representative == node.representative;
                }
                Require(containsRepresentative, "Sparse navigation representative is not a sparse descendant");
            }
        }
        finishLabel();
        Require(labelAt == labels.size() && offset == m_members.size(), "Incomplete sparse hierarchy coverage");
        for (std::size_t i = 0; i < counts.size(); ++i)
            Require(counts[i] <= m_header.caps[i], "Sparse hierarchy exceeds the source layer's posting-count cap");
        for (std::size_t i = 0; i < m_nodes.size(); ++i)
            Require((i + 1 == m_nodes.size()) ? m_nodes[i].parent == None && m_nodes[i].tag == None :
                incoming[i] == 1 && m_nodes[i].parent != None, "Sparse hierarchy is disconnected");
        for (int entry : m_entries)
            Require(m_nodes.empty() ? entry == -1 :
                entry >= 0 && entry < Count() && !IsLeaf(entry), "Invalid label-independent H1 entry");
    }

    void Refresh(VectorIndex& heads, const std::vector<Cache::NumQuantParam>& params)
    {
        m_words = params.size() * Cache::NUM_QUANT_WORDS;
        m_signatures.assign(m_nodes.size(), {});
        m_numeric.assign(m_nodes.size() * m_words, 0);
        for (int id = 0; id < Count(); ++id) {
            auto& signature = m_signatures[id];
            signature.Clear();
            if (Layered()) {
                for (auto row = LabelsBegin(id); row != LabelsEnd(id); ++row) signature.Insert(row->tag);
            } else if (IsLeaf(id)) signature.Insert(At(id).tag);
            for (auto p = Begin(id); p != End(id); ++p) {
                if (!Layered() && !IsLeaf(id)) signature.MergeOR(m_signatures[*p]);
                const auto* numeric = !m_words ? nullptr : IsLeaf(id) ? heads.GetHeadNodeNumQuant(*p) :
                    m_numeric.data() + *p * m_words;
                Require(!m_words || numeric, "Missing authenticated sparse posting numeric metadata");
                for (std::size_t word = 0; word < m_words; ++word)
                    m_numeric[id * m_words + word] |= numeric[word];
            }
        }
    }
    bool TerminalMayMatchLabels(int id, const std::vector<std::uint32_t>& labels) const
    {
        if (Layered()) {
            for (auto tag : labels) if (FindLabel(id, tag)) return true;
            return false;
        }
        const auto tag = At(id).tag;
        return tag == None || std::find(labels.begin(), labels.end(), tag) != labels.end();
    }
    bool MayMatch(int id, const std::vector<std::uint32_t>& labels, const RoutingPredicate& predicate) const
    {
        if (!TerminalMayMatchLabels(id, labels)) return false;
        bool matchingLabel = false;
        for (auto tag : labels)
            if (m_signatures.at(id).MayContain(tag)) { matchingLabel = true; break; }
        if (!matchingLabel) return false;
        // Only the routing-key label is summarized here; predicates on other
        // categorical columns remain conservative until native H1 admission.
        return predicate.MayMatch(nullptr, nullptr, Cache::HierWidthTable{},
            m_words ? m_numeric.data() + id * m_words : nullptr);
    }
    void Prefetch(int id) const
    {
        // Child label and representative metadata precede signature access.
        _mm_prefetch(reinterpret_cast<const char*>(&m_nodes[id]), _MM_HINT_T0);
    }
    std::uint64_t Fingerprint() const
    {
        Header header = m_header;
        header.nodes = static_cast<std::uint32_t>(m_nodes.size());
        header.members = m_members.size();
        if (Layered()) header.reserved2 = m_labelRows.size();
        header.fingerprint = 0;
        auto hash = SecondLevelHeadPostings::AddContentFingerprint(
            SecondLevelHeadPostings::BeginIDFingerprint(), &header, sizeof(header));
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, m_entries.data(), m_entries.size() * sizeof(int));
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, m_nodes.data(), m_nodes.size() * sizeof(Node));
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, m_members.data(), m_members.size() * sizeof(std::uint32_t));
        if (Layered()) hash = SecondLevelHeadPostings::AddContentFingerprint(
            hash, m_labelRows.data(), m_labelRows.size() * sizeof(LabelRow));
        if (Local()) {
            const auto policyHash = m_localAdmission.Hash();
            hash = SecondLevelHeadPostings::AddContentFingerprint(hash, &policyHash, sizeof(policyHash));
        }
        if (Sampled()) hash = SecondLevelHeadPostings::AddContentFingerprint(hash, &m_sizing, sizeof(m_sizing));
        return hash;
    }
    void Save(const std::string& path) const
    {
        Require(!Local() || m_localAdmission.Resident(), "Cannot export released local admission statistics");
        Require(!fileexists(path.c_str()), "Sparse hierarchy destination already exists");
        Header header = m_header;
        header.nodes = static_cast<std::uint32_t>(m_nodes.size());
        header.members = m_members.size();
        if (Layered()) header.reserved2 = m_labelRows.size();
        header.fingerprint = Fingerprint();
        std::ofstream out(path, std::ios::binary);
        out.write(reinterpret_cast<const char*>(&header), sizeof(header));
        out.write(reinterpret_cast<const char*>(m_entries.data()), m_entries.size() * sizeof(int));
        out.write(reinterpret_cast<const char*>(m_nodes.data()), m_nodes.size() * sizeof(Node));
        out.write(reinterpret_cast<const char*>(m_members.data()), m_members.size() * sizeof(std::uint32_t));
        if (Layered())
            out.write(reinterpret_cast<const char*>(m_labelRows.data()), m_labelRows.size() * sizeof(LabelRow));
        if (Local()) m_localAdmission.Save(out);
        if (Sampled()) out.write(reinterpret_cast<const char*>(&m_sizing), sizeof(m_sizing));
        out.close();
        Require(bool(out), "Cannot persist sparse hierarchy");
    }
    void Load(const std::string& path, const LimitedTagSupport& support, std::uint64_t headIDs,
              const Thresholds& thresholds, DimensionType dimension, VectorValueType type)
    {
        std::ifstream in(path, std::ios::binary | std::ios::ate);
        Require(bool(in), "Cannot open sparse hierarchy");
        const auto bytes = in.tellg();
        in.seekg(0);
        Header header;
        in.read(reinterpret_cast<char*>(&header), sizeof(header));
        Require(bool(in) && bytes >= std::streamoff(sizeof(Header)) &&
            header.magic == Header{}.magic && header.version >= 1 && header.version <= 6 &&
            header.bytes == sizeof(Header) && header.assignment == header.version - 1 &&
            (header.version >= 4 ? header.reserved2 <= static_cast<std::uint64_t>(MaxSize) &&
                header.reserved2 <= static_cast<std::uint64_t>(bytes) / sizeof(LabelRow) : !header.reserved2) &&
            header.heads > 0 && header.heads <= static_cast<std::uint32_t>(MaxSize) &&
            header.heads == static_cast<std::uint32_t>(support.HeadCount()) &&
            header.nodes <= static_cast<std::uint32_t>(MaxSize) &&
            header.members <= static_cast<std::uint64_t>(bytes) / sizeof(std::uint32_t) &&
            sizeof(Header) + std::uint64_t(header.heads) * sizeof(int) +
                std::uint64_t(header.nodes) * sizeof(Node) + header.members * sizeof(std::uint32_t) +
                header.reserved2 * sizeof(LabelRow) <=
                static_cast<std::uint64_t>(bytes), "Invalid sparse hierarchy header or exact file size");
        const auto bodyBytes = sizeof(Header) + std::uint64_t(header.heads) * sizeof(int) +
            std::uint64_t(header.nodes) * sizeof(Node) + header.members * sizeof(std::uint32_t) +
            header.reserved2 * sizeof(LabelRow);
        Require(header.version >= 5 || bodyBytes == static_cast<std::uint64_t>(bytes),
            "Trailing historical sparse hierarchy bytes");
        Initialize(header);
        m_nodes.resize(header.nodes);
        m_members.resize(header.members);
        in.read(reinterpret_cast<char*>(m_entries.data()), m_entries.size() * sizeof(int));
        in.read(reinterpret_cast<char*>(m_nodes.data()), m_nodes.size() * sizeof(Node));
        in.read(reinterpret_cast<char*>(m_members.data()), m_members.size() * sizeof(std::uint32_t));
        if (Layered()) {
            m_labelRows.resize(header.reserved2);
            in.read(reinterpret_cast<char*>(m_labelRows.data()), m_labelRows.size() * sizeof(LabelRow));
        }
        const auto sizingBytes = Sampled() ? sizeof(m_sizing) : 0;
        Require(static_cast<std::uint64_t>(bytes) - bodyBytes >= sizingBytes, "Truncated sampled sizing metadata");
        if (Local()) m_localAdmission.Load(in, static_cast<std::uint64_t>(bytes) - bodyBytes - sizingBytes, header.heads);
        if (Sampled()) in.read(reinterpret_cast<char*>(&m_sizing), sizeof(m_sizing));
        Require(bool(in) && Fingerprint() == header.fingerprint, "Sparse hierarchy authentication failed");
        Validate(support, headIDs, thresholds, dimension, type);
        BuildOwners();
    }
private:
    void BuildLayeredOwners()
    {
        m_ownerOffsets.assign(m_entries.size() + 1, 0);
        m_upperOwnerOffsets.assign(m_nodes.size() + 1, 0);
        std::vector<bool> existingEntry(m_entries.size(), false);
        std::vector<std::uint32_t> children;
        const auto visit = [&](const auto& emit) {
            for (int id = 0; id < Count(); ++id) {
                children.assign(Begin(id), End(id));
                std::sort(children.begin(), children.end());
                children.erase(std::unique(children.begin(), children.end()), children.end());
                for (auto child : children) emit(id, child, IsLeaf(id));
            }
        };
        visit([&](int id, std::uint32_t child, bool leaf) {
            auto& offsets = leaf ? m_ownerOffsets : m_upperOwnerOffsets;
            Require(child + 1 < offsets.size(), "Invalid layered reverse-owner child");
            ++offsets[child + 1];
            if (leaf && m_entries[child] == id) existingEntry[child] = true;
        });
        for (std::size_t head = 0; head < m_entries.size(); ++head)
            m_ownerOffsets[head + 1] += m_entries[head] >= 0 && !existingEntry[head];
        for (auto* offsets : {&m_ownerOffsets, &m_upperOwnerOffsets})
            for (std::size_t i = 1; i < offsets->size(); ++i) (*offsets)[i] += (*offsets)[i - 1];
        m_ownerIDs.resize(m_ownerOffsets.back());
        m_upperOwnerIDs.resize(m_upperOwnerOffsets.back());
        auto h1 = m_ownerOffsets, upper = m_upperOwnerOffsets;
        visit([&](int id, std::uint32_t child, bool leaf) {
            (leaf ? m_ownerIDs : m_upperOwnerIDs)[(leaf ? h1 : upper)[child]++] = id;
        });
        for (std::size_t head = 0; head < m_entries.size(); ++head)
            if (m_entries[head] >= 0 && !existingEntry[head]) m_ownerIDs[h1[head]++] = m_entries[head];
    }
    void ValidateLayered(const LimitedTagSupport& support) const
    {
        std::array<std::uint64_t, 4> counts{};
        std::array<std::uint64_t, 4> rowCounts{}, referenceCounts{};
        std::uint64_t memberOffset = 0, labelOffset = 0;
        int previousTier = 2;
        std::array<std::unordered_map<std::uint64_t, std::uint32_t>, 4> copies;
        std::array<std::unordered_set<std::uint32_t>, 4> representatives;
        const auto key = [](std::uint32_t child, std::uint32_t tag) {
            return (std::uint64_t(tag) << 32) | child;
        };
        for (int id = 0; id < Count(); ++id) {
            const auto& node = At(id);
            Require(node.representative < m_header.heads && node.tier >= 2 && node.tier <= 5 &&
                node.tier >= static_cast<unsigned>(previousTier) && node.tag != None &&
                node.begin == memberOffset && node.size > 0 && node.size <= static_cast<unsigned>(MaxSize) &&
                node.begin <= m_members.size() && node.size <= m_members.size() - node.begin &&
                node.parent == labelOffset && node.reserved > 0 && labelOffset <= m_labelRows.size() &&
                node.reserved <= m_labelRows.size() - labelOffset &&
                (support.HasExpansion() || node.reserved <= static_cast<unsigned>(support.SlotsPerHead())),
                "Invalid layered posting node, label slots or reference range");
            Require(representatives[node.tier - 2].insert(node.representative).second,
                "Layered physical representative was duplicated for labels");
            previousTier = node.tier;
            ++counts[node.tier - 2];
            rowCounts[node.tier - 2] += node.reserved;
            referenceCounts[node.tier - 2] += node.size;
            std::uint32_t previousTag = None;
            bool anchorPresent = false;
            for (auto row = LabelsBegin(id); row != LabelsEnd(id); ++row) {
                Require(row->tag != None && (previousTag == None || row->tag > previousTag) &&
                    (Local() || Admitted(support, row->tag, m_header.thresholds, node.tier)) &&
                    row->begin == memberOffset && row->size > 0 &&
                    row->size <= node.begin + node.size - memberOffset,
                    "Invalid layered label directory or selectivity admission");
                previousTag = row->tag;
                std::uint32_t previousChild = None;
                for (auto member = Begin(*row); member != End(*row); ++member) {
                    Require(previousChild == None || *member > previousChild,
                        "Duplicate or unordered child in layered label row");
                    previousChild = *member;
                    if (node.tier == 2) {
                        Require(*member < m_header.heads && support.Supports(*member, row->tag),
                            "Layered H2 contains unsupported H1 member");
                        anchorPresent |= row->tag == node.tag && *member == node.representative;
                    } else {
                        Require(*member < static_cast<unsigned>(id) && At(*member).tier + 1 == node.tier &&
                            FindLabel(*member, row->tag), "Layered posting skips a tier or invents child support");
                        anchorPresent |= row->tag == node.tag && At(*member).representative == node.representative;
                    }
                    Require(AdmitChild(support, node.tier == 2 ? *member : At(*member).representative,
                        row->tag, node.tier), "Layered row contains a locally nonadmitted child-label");
                    Require(++copies[node.tier - 2][key(*member, row->tag)] <= m_header.replicas,
                        "Layered per-child per-label replica cap exceeded");
                }
                memberOffset += row->size;
            }
            Require(memberOffset == node.begin + node.size && anchorPresent,
                "Layered posting does not contain its sparse anchor");
            labelOffset += node.reserved;
        }
        Require(memberOffset == m_members.size() && labelOffset == m_labelRows.size(),
            "Unreferenced layered members or label rows");
        for (int tier = 2; tier <= 5; ++tier) {
            Require(counts[tier - 2] <= m_header.caps[tier - 2], "Layered posting count exceeds source tier cap");
            std::vector<bool> eligible(Sampled() ? (tier == 2 ? m_header.heads : m_nodes.size()) : 0, false);
            std::uint64_t pairs = 0, distinct = 0;
            const auto covered = [&](std::uint32_t child, std::uint32_t tag) {
                if (AdmitChild(support, tier == 2 ? child : At(child).representative, tag, tier)) {
                    Require(copies[tier - 2].count(key(child, tag)) != 0,
                        "Layered assignment omitted an admitted child-label pair");
                    if (Sampled()) {
                        if (!eligible[child]) { eligible[child] = true; ++distinct; }
                        ++pairs;
                    }
                }
            };
            if (tier == 2) {
                for (const auto& entry : support.TagHeads())
                    for (auto head : entry.second) covered(head, entry.first);
            } else {
                for (int id = 0; id < Count(); ++id)
                    if (At(id).tier + 1 == static_cast<unsigned>(tier))
                        for (auto row = LabelsBegin(id); row != LabelsEnd(id); ++row) covered(id, row->tag);
            }
            if (Sampled()) {
                const auto& sizing = m_sizing.tiers[tier - 2];
                Require(sizing.children == distinct && sizing.pairs == pairs &&
                    sizing.parents == counts[tier - 2] && sizing.rows == rowCounts[tier - 2] &&
                    sizing.references == referenceCounts[tier - 2],
                    "Sampled sizing audit disagrees with actual layered coverage or rows");
            }
        }
        for (int entry : m_entries)
            Require(counts[0] == 0 ? entry == -1 :
                entry >= 0 && entry < Count() && At(entry).tier == 2, "Invalid layered H1 spatial entry");
    }
    Header m_header;
    std::vector<int> m_entries;
    std::vector<Node> m_nodes;
    std::vector<std::uint32_t> m_members;
    std::vector<std::uint64_t> m_ownerOffsets;
    std::vector<int> m_ownerIDs;
    std::vector<LabelRow> m_labelRows;
    std::vector<std::uint64_t> m_upperOwnerOffsets;
    std::vector<int> m_upperOwnerIDs;
    std::vector<Cache::PostingBitmask> m_signatures;
    std::vector<std::uint64_t> m_numeric;
    std::size_t m_words = 0;
    LocalLabelAdmission m_localAdmission;
    SampledHierarchySizing m_sizing;
    std::array<std::vector<std::uint32_t>, 4> m_queryLabels;
};

class SparsePostingParentFilter {
    const SparseLabelHierarchy& m_hierarchy;
    const std::vector<std::uint32_t>& m_labels;
    int m_highestTier = -1;
public:
    SparsePostingParentFilter(const SparseLabelHierarchy& hierarchy, const std::vector<std::uint32_t>& labels)
        : m_hierarchy(hierarchy), m_labels(labels) {}
    bool operator()(std::size_t, int id)
    {
        if (!m_hierarchy.Layered()) return true;
        // Resolve only after an H1 gap activates supplementation, never per graph visit.
        if (m_highestTier < 0) m_highestTier = m_hierarchy.HighestQueryTier(m_labels);
        return m_hierarchy.At(id).tier < static_cast<unsigned>(m_highestTier);
    }
};

struct SparsePostingLayout {
    static std::size_t NavigationLevels(const SparseLabelHierarchy& owners)
    {
        return owners.Metadata().thresholds.size();
    }
    static std::size_t NavigationLevel(const SparseLabelHierarchy& postings, std::size_t, int id)
    {
        return postings.At(id).tier - 2;
    }
    static bool PreferResultAnchors(const SparseLabelHierarchy& postings) { return postings.Metadata().version >= 3; }
    static bool IsLeaf(const SparseLabelHierarchy& postings, std::size_t, int id) { return postings.IsLeaf(id); }
    static bool IsH2(const SparseLabelHierarchy& postings, std::size_t, int id) { return postings.At(id).tier == 2; }
    static std::size_t ChildLevel(std::size_t) { return 0; }
    template<class Visit>
    static void VisitParents(const SparseLabelHierarchy& owners, std::size_t, int id, Visit visit)
    {
        owners.VisitUpperParents(id, [&](int parent) { visit(0, parent); });
    }
};
}}

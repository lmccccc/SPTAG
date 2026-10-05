// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/SecondLevelHeadPostings.h"
#include <array>
#include <fstream>
#include <algorithm>
#include <stdexcept>
#include <vector>

namespace SPTAG { namespace SPANN {

class LocalLabelAdmission
{
    struct Header {
        std::uint64_t magic = 0x314C41434F4C5053ULL;
        std::uint32_t version = 1, regions = 0, labels = 0, heads = 0;
        std::uint64_t bytes = 0;
    };
    static_assert(sizeof(Header) == 32, "Local admission header layout");
    std::uint64_t m_releasedHash = 0;
    bool m_released = false;
    Header Metadata() const
    {
        Header header;
        header.heads = home.size();
        header.labels = labels.size();
        header.regions = windows.size();
        header.bytes = sizeof(Header) + std::uint64_t(header.heads) * 4 +
            std::uint64_t(header.labels) * 4 + std::uint64_t(header.regions) * 16 + masks.size();
        return header;
    }
public:
    std::vector<std::uint32_t> home, labels;
    std::vector<std::array<std::uint32_t, 4>> windows;
    std::vector<std::uint8_t> masks;
    bool Resident() const { return !m_released; }

    static void Require(bool condition, const char* message)
    {
        if (!condition) throw std::runtime_error(message);
    }
    bool Admits(std::uint32_t head, std::uint32_t label, int tier) const
    {
        Require(!m_released && head < home.size() && tier >= 2 && tier <= 5,
            "Local admission requires authenticated build statistics");
        const auto at = std::lower_bound(labels.begin(), labels.end(), label);
        return at != labels.end() && *at == label &&
            (masks[std::size_t(home[head]) * labels.size() + (at - labels.begin())] & (1U << (tier - 2)));
    }
    void Validate(std::uint32_t heads) const
    {
        Require(!m_released && home.size() == heads && !windows.empty() &&
            windows.size() <= heads && !labels.empty() && labels.size() <= MaxSize &&
            masks.size() == windows.size() * labels.size() &&
            std::is_sorted(labels.begin(), labels.end()) &&
            std::adjacent_find(labels.begin(), labels.end()) == labels.end() && labels.back() != UINT32_MAX,
            "Invalid local admission shape or label domain");
        for (auto region : home) Require(region < windows.size(), "Invalid local spatial address");
        for (const auto& row : windows)
            Require(row[0] > 0 && row[3] <= heads && std::is_sorted(row.begin(), row.end()),
                "Local spatial windows must have nested, nonzero H1 mass");
        for (auto mask : masks)
            Require(mask <= 15 && (mask & (mask + 1)) == 0,
                "Local admission must stop monotonically as the spatial window grows");
    }
    std::uint64_t Hash() const
    {
        if (m_released) return m_releasedHash;
        const auto header = Metadata();
        auto hash = SecondLevelHeadPostings::AddContentFingerprint(
            SecondLevelHeadPostings::BeginIDFingerprint(), &header, sizeof(header));
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, home.data(), home.size() * 4);
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, labels.data(), labels.size() * 4);
        hash = SecondLevelHeadPostings::AddContentFingerprint(hash, windows.data(), windows.size() * 16);
        return SecondLevelHeadPostings::AddContentFingerprint(hash, masks.data(), masks.size());
    }
    void Save(std::ostream& out) const
    {
        Require(!m_released, "Cannot export released local admission statistics");
        const auto header = Metadata();
        out.write(reinterpret_cast<const char*>(&header), sizeof(header));
        out.write(reinterpret_cast<const char*>(home.data()), home.size() * 4);
        out.write(reinterpret_cast<const char*>(labels.data()), labels.size() * 4);
        out.write(reinterpret_cast<const char*>(windows.data()), windows.size() * 16);
        out.write(reinterpret_cast<const char*>(masks.data()), masks.size());
    }
    void Load(std::istream& in, std::uint64_t bytes, std::uint32_t heads)
    {
        Header header;
        Require(bytes >= sizeof(header), "Truncated local admission header");
        in.read(reinterpret_cast<char*>(&header), sizeof(header));
        Require(bool(in) && header.magic == Header{}.magic && header.version == 1 &&
            header.heads == heads && header.regions > 0 && header.regions <= heads &&
            header.labels > 0 && header.labels <= MaxSize && header.bytes == bytes &&
            sizeof(header) + std::uint64_t(heads) * 4 + std::uint64_t(header.labels) * 4 +
                std::uint64_t(header.regions) * 16 +
                std::uint64_t(header.regions) * header.labels == bytes,
            "Invalid local admission header or exact file size");
        m_releasedHash = 0;
        m_released = false;
        home.resize(heads); labels.resize(header.labels); windows.resize(header.regions);
        masks.resize(std::size_t(header.regions) * header.labels);
        in.read(reinterpret_cast<char*>(home.data()), home.size() * 4);
        in.read(reinterpret_cast<char*>(labels.data()), labels.size() * 4);
        in.read(reinterpret_cast<char*>(windows.data()), windows.size() * 16);
        in.read(reinterpret_cast<char*>(masks.data()), masks.size());
        Require(bool(in), "Truncated local admission payload");
        Validate(heads);
    }
    void Release()
    {
        m_releasedHash = Hash();
        m_released = true;
        std::vector<std::uint32_t>().swap(home);
        std::vector<std::uint32_t>().swap(labels);
        std::vector<std::array<std::uint32_t, 4>>().swap(windows);
        std::vector<std::uint8_t>().swap(masks);
    }
};

}}

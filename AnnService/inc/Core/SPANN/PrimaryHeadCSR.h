#pragma once

#include <cstdint>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace SPTAG::SPANN
{
    static constexpr std::uint64_t kPrimaryHeadCSRMagic =
        0x3152534348444850ULL; // "PHDHCSR1"
    static constexpr std::uint32_t kPrimaryHeadCSRVersion = 2;

#pragma pack(push, 1)
    struct PrimaryHeadCSRHeader
    {
        std::uint64_t magic = kPrimaryHeadCSRMagic;
        std::uint32_t version = kPrimaryHeadCSRVersion;
        std::uint32_t headCount = 0;
        std::uint64_t entryCount = 0;
        std::uint32_t attributeCount = 0;
        std::uint32_t reserved[4] = {};
    };

    struct LegacyPrimaryHeadCSRHeader
    {
        std::uint64_t magic = kPrimaryHeadCSRMagic;
        std::uint32_t version = 1;
        std::uint32_t headCount = 0;
        std::uint64_t entryCount = 0;
        std::uint32_t tagBases[4] = {};
        std::uint32_t reserved = 0;
    };

    struct LegacyPrimaryHeadCSREntry
    {
        std::uint32_t vid = 0;
        std::uint64_t attributes = 0;
    };
#pragma pack(pop)

    static_assert(
        sizeof(PrimaryHeadCSRHeader) == sizeof(LegacyPrimaryHeadCSRHeader),
        "primary CSR headers must remain version-readable");

    class PrimaryHeadCSR
    {
    public:
        bool Load(const std::string& path, std::uint32_t expectedHeadCount)
        {
            std::ifstream input(path, std::ios::binary);
            if (!input) return false;

            std::uint64_t magic = 0;
            std::uint32_t version = 0;
            input.read(reinterpret_cast<char*>(&magic), sizeof(magic));
            input.read(reinterpret_cast<char*>(&version), sizeof(version));
            if (!input || magic != kPrimaryHeadCSRMagic ||
                (version != 1 && version != kPrimaryHeadCSRVersion)) {
                return false;
            }
            input.seekg(0, std::ios::beg);
            return version == 1
                ? LoadLegacy(input, expectedHeadCount)
                : LoadCurrent(input, expectedHeadCount);
        }

        bool Loaded() const { return !m_offsets.empty(); }

        std::uint32_t HeadCount() const { return m_header.headCount; }

        std::uint32_t AttributeCount() const {
            return m_header.attributeCount;
        }

        const PrimaryHeadCSRHeader& Header() const { return m_header; }

        std::uint32_t Begin(std::uint32_t headId) const
        {
            return headId < m_header.headCount ? m_offsets[headId] : 0;
        }

        std::uint32_t End(std::uint32_t headId) const
        {
            return headId < m_header.headCount ? m_offsets[headId + 1] : 0;
        }

        std::uint32_t Vid(std::uint32_t entry) const
        {
            return m_vids[entry];
        }

        const std::uint32_t* Attributes(std::uint32_t entry) const
        {
            return m_attributes.data() +
                static_cast<std::size_t>(entry) * m_header.attributeCount;
        }

        bool MatchesAnyValue(std::uint32_t entry, std::uint32_t value) const
        {
            const std::uint32_t* row = Attributes(entry);
            for (std::uint32_t column = 0;
                 column < m_header.attributeCount;
                 ++column) {
                if (row[column] == value) return true;
            }
            return false;
        }

    private:
        bool ReadOffsets(std::ifstream& input)
        {
            m_offsets.resize(static_cast<std::size_t>(m_header.headCount) + 1);
            input.read(
                reinterpret_cast<char*>(m_offsets.data()),
                static_cast<std::streamsize>(
                    m_offsets.size() * sizeof(std::uint32_t)));
            return input && m_offsets.front() == 0 &&
                m_offsets.back() == m_header.entryCount;
        }

        bool LoadCurrent(std::ifstream& input,
                         std::uint32_t expectedHeadCount)
        {
            PrimaryHeadCSRHeader header;
            input.read(reinterpret_cast<char*>(&header), sizeof(header));
            if (!input || header.magic != kPrimaryHeadCSRMagic ||
                header.version != kPrimaryHeadCSRVersion ||
                header.headCount != expectedHeadCount ||
                header.entryCount >
                    std::numeric_limits<std::uint32_t>::max() ||
                header.attributeCount == 0 ||
                header.attributeCount > 4096) {
                return false;
            }
            m_header = header;
            if (!ReadOffsets(input)) return false;

            m_vids.resize(static_cast<std::size_t>(header.entryCount));
            input.read(
                reinterpret_cast<char*>(m_vids.data()),
                static_cast<std::streamsize>(
                    m_vids.size() * sizeof(std::uint32_t)));
            if (!input ||
                header.entryCount >
                    std::numeric_limits<std::size_t>::max() /
                        header.attributeCount) {
                return false;
            }
            m_attributes.resize(
                static_cast<std::size_t>(header.entryCount) *
                header.attributeCount);
            input.read(
                reinterpret_cast<char*>(m_attributes.data()),
                static_cast<std::streamsize>(
                    m_attributes.size() * sizeof(std::uint32_t)));
            return static_cast<bool>(input);
        }

        bool LoadLegacy(std::ifstream& input,
                        std::uint32_t expectedHeadCount)
        {
            LegacyPrimaryHeadCSRHeader header;
            input.read(reinterpret_cast<char*>(&header), sizeof(header));
            if (!input || header.magic != kPrimaryHeadCSRMagic ||
                header.version != 1 ||
                header.headCount != expectedHeadCount ||
                header.entryCount >
                    std::numeric_limits<std::uint32_t>::max()) {
                return false;
            }
            m_header = {};
            m_header.headCount = header.headCount;
            m_header.entryCount = header.entryCount;
            m_header.attributeCount = 5;
            if (!ReadOffsets(input)) return false;

            std::vector<LegacyPrimaryHeadCSREntry> entries(
                static_cast<std::size_t>(header.entryCount));
            input.read(
                reinterpret_cast<char*>(entries.data()),
                static_cast<std::streamsize>(
                    entries.size() * sizeof(LegacyPrimaryHeadCSREntry)));
            if (!input) return false;

            m_vids.resize(entries.size());
            m_attributes.resize(entries.size() * 5);
            for (std::size_t entry = 0; entry < entries.size(); ++entry) {
                m_vids[entry] = entries[entry].vid;
                const std::uint32_t packed =
                    static_cast<std::uint32_t>(entries[entry].attributes);
                for (int column = 0; column < 4; ++column) {
                    m_attributes[entry * 5 + column] =
                        header.tagBases[column] +
                        ((packed >> (column * 8)) & 0xffU);
                }
                m_attributes[entry * 5 + 4] =
                    static_cast<std::uint32_t>(
                        entries[entry].attributes >> 32);
            }
            return true;
        }

        PrimaryHeadCSRHeader m_header;
        std::vector<std::uint32_t> m_offsets;
        std::vector<std::uint32_t> m_vids;
        std::vector<std::uint32_t> m_attributes;
    };
}

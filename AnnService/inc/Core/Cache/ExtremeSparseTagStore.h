// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_CACHE_EXTREMESPARSETAGSTORE_H_
#define _SPTAG_CACHE_EXTREMESPARSETAGSTORE_H_

#include "inc/Core/Common.h"
#include "inc/Helper/AtomicFile.h"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#ifdef _MSC_VER
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace SPTAG
{
namespace Cache
{

class ExtremeSparseTagStore
{
public:
#pragma pack(push, 1)
    struct Header
    {
        std::uint32_t m_magic = 0x31545345U; // EST1
        std::uint32_t m_version = 3;
        std::uint32_t m_headerBytes = 96;
        std::uint32_t m_directoryEntryBytes = 24;
        std::uint32_t m_valueType = 0;
        std::uint32_t m_dimension = 0;
        std::uint32_t m_attributeCount = 0;
        std::uint32_t m_keyColumn = 0;
        std::uint32_t m_vectorBytes = 0;
        std::uint32_t m_recordBytes = 0;
        std::uint64_t m_vectorCount = 0;
        std::uint64_t m_tagCount = 0;
        std::uint64_t m_recordCount = 0;
        std::uint64_t m_dataOffset = 0;
        std::uint64_t m_generationFingerprint = 0;
        std::uint64_t m_bodyFingerprint = 0;
        double m_maxSelectivity = 0.0;
    };

    struct DirectoryEntry
    {
        std::uint32_t m_tag = 0;
        std::uint32_t m_reserved = 0;
        std::uint64_t m_offset = 0;
        std::uint64_t m_count = 0;
    };
#pragma pack(pop)

    static_assert(sizeof(Header) == 96,
                  "ExtremeSparseTagStore header layout changed");
    static_assert(sizeof(DirectoryEntry) == 24,
                  "ExtremeSparseTagStore directory layout changed");

    ExtremeSparseTagStore() = default;
    ExtremeSparseTagStore(const ExtremeSparseTagStore&) = delete;
    ExtremeSparseTagStore& operator=(const ExtremeSparseTagStore&) = delete;

    ~ExtremeSparseTagStore()
    {
        Reset();
    }

    void Reset()
    {
#ifdef _MSC_VER
        if (m_fileHandle !=
            INVALID_HANDLE_VALUE)
        {
            CloseHandle(m_fileHandle);
            m_fileHandle =
                INVALID_HANDLE_VALUE;
        }
#else
        if (m_fileDescriptor >= 0)
        {
            close(m_fileDescriptor);
            m_fileDescriptor = -1;
        }
#endif
        m_header = Header();
        m_directory.clear();
        m_path.clear();
    }

    bool Build(const std::string& p_path,
               const std::uint32_t* p_attributes,
               std::uint64_t p_vectorCount,
               std::uint32_t p_attributeCount,
               std::uint32_t p_keyColumn,
               const std::uint8_t* p_vectors,
               std::uint64_t p_vectorBufferBytes,
               std::uint32_t p_vectorBytes,
               VectorValueType p_valueType,
               std::uint32_t p_dimension,
               double p_maxSelectivity,
               std::uint64_t p_generationFingerprint,
               std::string* p_error = nullptr)
    {
        Reset();
        const auto fail = [&](const std::string& p_message) {
            if (p_error != nullptr) *p_error = p_message;
            return false;
        };
        if (p_path.empty() || p_attributes == nullptr || p_vectors == nullptr ||
            p_vectorCount == 0 || p_attributeCount == 0 ||
            p_keyColumn >= p_attributeCount || p_vectorBytes == 0 ||
            p_dimension == 0 || !std::isfinite(p_maxSelectivity) ||
            p_maxSelectivity <= 0.0 || p_maxSelectivity > 1.0 ||
            p_generationFingerprint == 0)
        {
            return fail("invalid extreme-sparse tag build parameters");
        }
        const size_t valueBytes = GetValueTypeSize(p_valueType);
        if (valueBytes == 0 ||
            p_dimension >
                (std::numeric_limits<std::uint32_t>::max)() /
                    valueBytes ||
            p_vectorBytes != p_dimension * valueBytes ||
            p_vectorCount >
                static_cast<std::uint64_t>(
                    (std::numeric_limits<std::int32_t>::max)()) ||
            p_vectorCount >
                (std::numeric_limits<size_t>::max)() /
                    p_attributeCount)
        {
            return fail(
                "extreme-sparse vector type or count is unsupported");
        }
        if (p_vectorCount >
            (std::numeric_limits<std::uint64_t>::max)() /
                p_vectorBytes ||
            p_vectorBufferBytes != p_vectorCount * p_vectorBytes)
        {
            return fail("extreme-sparse vector buffer size mismatch");
        }
        const std::uint64_t attributeBytes =
            static_cast<std::uint64_t>(p_attributeCount) *
            sizeof(std::uint32_t);
        const std::uint64_t recordBytes =
            sizeof(std::int32_t) + attributeBytes + p_vectorBytes;
        if (recordBytes >
            (std::numeric_limits<std::uint32_t>::max)())
        {
            return fail("extreme-sparse record is too large");
        }

        std::unordered_map<std::uint32_t, std::uint64_t> counts;
        counts.reserve(static_cast<size_t>((std::min<std::uint64_t>)(
            p_vectorCount, 1ULL << 20)));
        for (std::uint64_t vector = 0; vector < p_vectorCount; ++vector)
        {
            const std::uint32_t tag =
                p_attributes[
                    vector * p_attributeCount + p_keyColumn];
            ++counts[tag];
        }

        m_directory.reserve(counts.size());
        for (const auto& entry : counts)
        {
            const double selectivity =
                static_cast<double>(entry.second) /
                static_cast<double>(p_vectorCount);
            if (selectivity <= p_maxSelectivity)
            {
                DirectoryEntry directoryEntry;
                directoryEntry.m_tag = entry.first;
                directoryEntry.m_count = entry.second;
                m_directory.push_back(directoryEntry);
            }
        }
        std::sort(
            m_directory.begin(), m_directory.end(),
            [](const DirectoryEntry& p_left,
               const DirectoryEntry& p_right) {
                return p_left.m_tag < p_right.m_tag;
            });

        m_header.m_valueType =
            static_cast<std::uint32_t>(p_valueType);
        m_header.m_dimension = p_dimension;
        m_header.m_attributeCount = p_attributeCount;
        m_header.m_keyColumn = p_keyColumn;
        m_header.m_vectorBytes = p_vectorBytes;
        m_header.m_recordBytes =
            static_cast<std::uint32_t>(recordBytes);
        m_header.m_vectorCount = p_vectorCount;
        m_header.m_tagCount = m_directory.size();
        m_header.m_generationFingerprint =
            p_generationFingerprint;
        m_header.m_maxSelectivity = p_maxSelectivity;
        if (m_directory.size() >
            ((std::numeric_limits<std::uint64_t>::max)() -
             sizeof(Header)) /
                sizeof(DirectoryEntry))
        {
            return fail("extreme-sparse directory size overflow");
        }
        m_header.m_dataOffset =
            sizeof(Header) +
            static_cast<std::uint64_t>(m_directory.size()) *
                sizeof(DirectoryEntry);

        std::uint64_t recordCursor = 0;
        for (auto& entry : m_directory)
        {
            if (recordCursor >
                (std::numeric_limits<std::uint64_t>::max)() -
                    entry.m_count)
            {
                return fail("extreme-sparse record count overflow");
            }
            entry.m_offset =
                m_header.m_dataOffset +
                recordCursor * recordBytes;
            recordCursor += entry.m_count;
        }
        m_header.m_recordCount = recordCursor;
        if (recordCursor >
            ((std::numeric_limits<std::uint64_t>::max)() -
             m_header.m_dataOffset) /
                recordBytes)
        {
            return fail("extreme-sparse file size overflow");
        }
        const std::uint64_t fileBytes =
            m_header.m_dataOffset + recordCursor * recordBytes;
        if (fileBytes >
            static_cast<std::uint64_t>(
                (std::numeric_limits<std::streamoff>::max)()))
        {
            return fail("extreme-sparse file exceeds stream limits");
        }

        const std::string temporary = p_path + ".tmp";
        std::fstream output(
            temporary,
            std::ios::binary | std::ios::in |
                std::ios::out | std::ios::trunc);
        if (!output)
        {
            return fail("cannot open extreme-sparse tag output");
        }
        output.write(
            reinterpret_cast<const char*>(&m_header),
            sizeof(m_header));
        if (!m_directory.empty())
        {
            output.write(
                reinterpret_cast<const char*>(m_directory.data()),
                static_cast<std::streamsize>(
                    m_directory.size() *
                    sizeof(DirectoryEntry)));
        }
        if (fileBytes > 0)
        {
            output.seekp(
                static_cast<std::streamoff>(fileBytes - 1));
            const char zero = 0;
            output.write(&zero, 1);
        }
        if (!output)
        {
            output.close();
            std::remove(temporary.c_str());
            return fail("cannot allocate extreme-sparse tag output");
        }

        std::unordered_map<std::uint32_t, size_t> entryByTag;
        entryByTag.reserve(m_directory.size() * 2 + 1);
        for (size_t entry = 0; entry < m_directory.size(); ++entry)
        {
            entryByTag.emplace(m_directory[entry].m_tag, entry);
        }
        std::vector<std::uint64_t> cursors(m_directory.size(), 0);
        for (std::uint64_t vector = 0; vector < p_vectorCount; ++vector)
        {
            const std::uint32_t tag =
                p_attributes[
                    vector * p_attributeCount + p_keyColumn];
            const auto found = entryByTag.find(tag);
            if (found == entryByTag.end()) continue;
            const size_t entryIndex = found->second;
            if (cursors[entryIndex] >=
                m_directory[entryIndex].m_count)
            {
                output.close();
                std::remove(temporary.c_str());
                return fail("extreme-sparse tag write cursor overflow");
            }
            const std::uint64_t offset =
                m_directory[entryIndex].m_offset +
                cursors[entryIndex] * recordBytes;
            output.seekp(static_cast<std::streamoff>(offset));
            const std::int32_t vectorID =
                static_cast<std::int32_t>(vector);
            output.write(
                reinterpret_cast<const char*>(&vectorID),
                sizeof(vectorID));
            output.write(
                reinterpret_cast<const char*>(
                    p_attributes +
                    vector * p_attributeCount),
                static_cast<std::streamsize>(attributeBytes));
            output.write(
                reinterpret_cast<const char*>(
                    p_vectors + vector * p_vectorBytes),
                p_vectorBytes);
            ++cursors[entryIndex];
            if (!output)
            {
                output.close();
                std::remove(temporary.c_str());
                return fail("cannot write extreme-sparse tag record");
            }
        }
        output.flush();
        output.close();
        if (!output)
        {
            std::remove(temporary.c_str());
            return fail("cannot flush extreme-sparse tag output");
        }
        for (size_t entry = 0; entry < cursors.size(); ++entry)
        {
            if (cursors[entry] != m_directory[entry].m_count)
            {
                std::remove(temporary.c_str());
                return fail("extreme-sparse tag record count mismatch");
            }
        }

        if (!FingerprintFile(
                temporary, m_header,
                m_header.m_bodyFingerprint))
        {
            std::remove(temporary.c_str());
            return fail("cannot fingerprint extreme-sparse tag output");
        }
        {
            std::fstream headerOutput(
                temporary,
                std::ios::binary | std::ios::in | std::ios::out);
            if (!headerOutput ||
                !headerOutput.write(
                    reinterpret_cast<const char*>(&m_header),
                    sizeof(m_header)))
            {
                headerOutput.close();
                std::remove(temporary.c_str());
                return fail("cannot finalize extreme-sparse tag header");
            }
        }
        if (!Helper::AtomicReplaceFile(
                temporary, p_path))
        {
            std::remove(temporary.c_str());
            return fail(
                "cannot publish extreme-sparse tag output: " +
                std::string(std::strerror(errno)));
        }
        return Load(p_path, p_error);
    }

    bool Load(const std::string& p_path,
              std::string* p_error = nullptr)
    {
        Reset();
        const auto fail = [&](const std::string& p_message) {
            if (p_error != nullptr) *p_error = p_message;
            Reset();
            return false;
        };
        if (!OpenRetainedFile(p_path))
        {
            return fail("cannot open extreme-sparse tag input");
        }
        std::uint64_t fileBytes = 0;
        if (!RetainedFileSize(fileBytes) ||
            !ReadRetained(
                0, &m_header,
                sizeof(m_header)) ||
            m_header.m_magic != 0x31545345U ||
                m_header.m_version != Header().m_version ||
            m_header.m_headerBytes != sizeof(Header) ||
            m_header.m_directoryEntryBytes !=
                sizeof(DirectoryEntry) ||
            m_header.m_dimension == 0 ||
            m_header.m_attributeCount == 0 ||
            m_header.m_keyColumn >=
                m_header.m_attributeCount ||
            m_header.m_vectorBytes == 0 ||
            m_header.m_recordBytes !=
                sizeof(std::int32_t) +
                    m_header.m_attributeCount *
                        sizeof(std::uint32_t) +
                    m_header.m_vectorBytes ||
            m_header.m_generationFingerprint == 0 ||
            !std::isfinite(m_header.m_maxSelectivity) ||
            m_header.m_maxSelectivity <= 0.0 ||
            m_header.m_maxSelectivity > 1.0)
        {
            return fail("invalid extreme-sparse tag header");
        }
        const auto storedType =
            static_cast<VectorValueType>(
                static_cast<std::uint8_t>(
                    m_header.m_valueType));
        const size_t valueBytes =
            GetValueTypeSize(storedType);
        if (m_header.m_valueType >
                (std::numeric_limits<std::uint8_t>::max)() ||
            valueBytes == 0 ||
            m_header.m_dimension >
                (std::numeric_limits<std::uint32_t>::max)() /
                    valueBytes ||
            m_header.m_vectorBytes !=
                m_header.m_dimension * valueBytes ||
            m_header.m_vectorCount >
                static_cast<std::uint64_t>(
                    (std::numeric_limits<std::int32_t>::max)()))
        {
            return fail(
                "invalid extreme-sparse vector layout");
        }
        const std::uint64_t maxDirectoryCount =
            ((std::numeric_limits<std::uint64_t>::max)() -
             sizeof(Header)) /
            sizeof(DirectoryEntry);
        if (m_header.m_tagCount >
                maxDirectoryCount ||
            m_header.m_tagCount >
                static_cast<std::uint64_t>(
                    m_directory.max_size()))
        {
            return fail("extreme-sparse tag directory is too large");
        }
        const std::uint64_t expectedDataOffset =
            sizeof(Header) +
            m_header.m_tagCount * sizeof(DirectoryEntry);
        if (m_header.m_dataOffset != expectedDataOffset ||
            m_header.m_recordCount >
                ((std::numeric_limits<std::uint64_t>::max)() -
                 m_header.m_dataOffset) /
                    m_header.m_recordBytes)
        {
            return fail("invalid extreme-sparse tag offsets");
        }
        const std::uint64_t expectedFileBytes =
            m_header.m_dataOffset +
            m_header.m_recordCount *
                m_header.m_recordBytes;
        if (fileBytes != expectedFileBytes)
        {
            return fail("extreme-sparse tag file size mismatch");
        }

        m_directory.resize(
            static_cast<size_t>(m_header.m_tagCount));
        if (!m_directory.empty() &&
            !ReadRetained(
                sizeof(Header),
                m_directory.data(),
                m_directory.size() *
                    sizeof(DirectoryEntry)))
        {
            return fail("cannot read extreme-sparse tag directory");
        }
        std::uint64_t expectedOffset = m_header.m_dataOffset;
        std::uint64_t countedRecords = 0;
        std::uint32_t previousTag = 0;
        bool hasPrevious = false;
        for (const auto& entry : m_directory)
        {
            if ((hasPrevious && entry.m_tag <= previousTag) ||
                entry.m_reserved != 0 ||
                entry.m_count == 0 ||
                entry.m_offset != expectedOffset ||
                entry.m_count >
                    ((std::numeric_limits<std::uint64_t>::max)() -
                     expectedOffset) /
                        m_header.m_recordBytes)
            {
                return fail("invalid extreme-sparse tag directory entry");
            }
            previousTag = entry.m_tag;
            hasPrevious = true;
            expectedOffset +=
                entry.m_count * m_header.m_recordBytes;
            if (countedRecords >
                (std::numeric_limits<std::uint64_t>::max)() -
                    entry.m_count)
            {
                return fail(
                    "extreme-sparse tag directory count overflow");
            }
            countedRecords += entry.m_count;
        }
        if (countedRecords != m_header.m_recordCount ||
            expectedOffset != expectedFileBytes)
        {
            return fail("extreme-sparse tag directory count mismatch");
        }
        std::uint64_t fingerprint = 0;
        if (!FingerprintRetained(
                m_header, fileBytes,
                fingerprint) ||
            fingerprint != m_header.m_bodyFingerprint)
        {
            return fail("extreme-sparse tag body fingerprint mismatch");
        }
        m_path = p_path;
        return true;
    }

    bool ValidateExpected(VectorValueType p_valueType,
                          std::uint32_t p_dimension,
                          std::uint32_t p_attributeCount,
                          std::uint32_t p_keyColumn,
                          std::uint64_t p_vectorCount,
                          std::uint64_t p_generationFingerprint,
                          double p_maxSelectivity,
                          std::string* p_error = nullptr) const
    {
        if (m_path.empty() ||
            m_header.m_valueType !=
                static_cast<std::uint32_t>(p_valueType) ||
            m_header.m_dimension != p_dimension ||
            m_header.m_attributeCount != p_attributeCount ||
            m_header.m_keyColumn != p_keyColumn ||
            m_header.m_vectorCount != p_vectorCount ||
            m_header.m_generationFingerprint !=
                p_generationFingerprint ||
            m_header.m_maxSelectivity != p_maxSelectivity)
        {
            if (p_error != nullptr)
            {
                *p_error =
                    "extreme-sparse tag store does not match the loaded index";
            }
            return false;
        }
        return true;
    }

    std::uint64_t GenerationFingerprint() const
    {
        return m_header.m_generationFingerprint;
    }

    double MaxSelectivity() const
    {
        return m_header.m_maxSelectivity;
    }

    const DirectoryEntry* Find(std::uint32_t p_tag) const
    {
        const auto found = std::lower_bound(
            m_directory.begin(), m_directory.end(), p_tag,
            [](const DirectoryEntry& p_entry,
               std::uint32_t p_value) {
                return p_entry.m_tag < p_value;
            });
        return found != m_directory.end() &&
                       found->m_tag == p_tag
            ? &*found
            : nullptr;
    }

    bool Read(const std::vector<std::uint32_t>& p_tags,
              std::vector<std::uint8_t>& p_records,
              std::uint64_t& p_recordCount,
              std::string* p_error = nullptr) const
    {
        p_records.clear();
        p_recordCount = 0;
        if (m_path.empty())
        {
            if (p_error != nullptr)
                *p_error = "extreme-sparse tag store is not loaded";
            return false;
        }
        std::vector<std::uint32_t> tags = p_tags;
        std::sort(tags.begin(), tags.end());
        tags.erase(
            std::unique(tags.begin(), tags.end()),
            tags.end());
        std::vector<const DirectoryEntry*> entries;
        entries.reserve(tags.size());
        for (std::uint32_t tag : tags)
        {
            const auto* entry = Find(tag);
            if (entry == nullptr) continue;
            if (p_recordCount >
                (std::numeric_limits<std::uint64_t>::max)() -
                    entry->m_count)
            {
                if (p_error != nullptr)
                    *p_error = "extreme-sparse query count overflow";
                return false;
            }
            p_recordCount += entry->m_count;
            entries.push_back(entry);
        }
        if (p_recordCount == 0) return true;
        if (p_recordCount >
            (std::numeric_limits<size_t>::max)() /
                m_header.m_recordBytes)
        {
            if (p_error != nullptr)
                *p_error = "extreme-sparse query buffer is too large";
            return false;
        }
        p_records.resize(
            static_cast<size_t>(p_recordCount) *
            m_header.m_recordBytes);
        size_t outputOffset = 0;
        for (const auto* entry : entries)
        {
            const size_t bytes =
                static_cast<size_t>(entry->m_count) *
                m_header.m_recordBytes;
            if (!ReadRetained(
                    entry->m_offset,
                    p_records.data() + outputOffset,
                    bytes))
            {
                if (p_error != nullptr)
                    *p_error = "cannot read extreme-sparse tag range";
                return false;
            }
            outputOffset += bytes;
        }
        return true;
    }

    std::uint32_t AttributeCount() const
    {
        return m_header.m_attributeCount;
    }

    std::uint32_t KeyColumn() const
    {
        return m_header.m_keyColumn;
    }

    std::uint32_t VectorBytes() const
    {
        return m_header.m_vectorBytes;
    }

    std::uint32_t RecordBytes() const
    {
        return m_header.m_recordBytes;
    }

    std::uint64_t VectorCount() const
    {
        return m_header.m_vectorCount;
    }

    std::uint64_t StoredRecordCount() const
    {
        return m_header.m_recordCount;
    }

private:
    static void UpdateFingerprint(
        const char* p_data,
        size_t p_bytes,
        std::uint64_t& p_fingerprint)
    {
        constexpr std::uint64_t kPrime =
            1099511628211ULL;
        for (size_t index = 0;
             index < p_bytes; ++index)
        {
            p_fingerprint ^=
                static_cast<std::uint8_t>(
                    p_data[index]);
            p_fingerprint *= kPrime;
        }
    }

    static bool FingerprintFile(
        const std::string& p_path,
        Header p_header,
        std::uint64_t& p_fingerprint)
    {
        std::ifstream input(p_path, std::ios::binary);
        if (!input) return false;
        input.seekg(
            static_cast<std::streamoff>(
                sizeof(Header)));
        if (!input) return false;
        constexpr std::uint64_t kOffset =
            1469598103934665603ULL;
        std::uint64_t hash = kOffset;
        p_header.m_bodyFingerprint = 0;
        UpdateFingerprint(
            reinterpret_cast<const char*>(
                &p_header),
            sizeof(p_header), hash);
        std::vector<char> buffer(1 << 20);
        while (input)
        {
            input.read(
                buffer.data(),
                static_cast<std::streamsize>(buffer.size()));
            const std::streamsize count = input.gcount();
            UpdateFingerprint(
                buffer.data(),
                static_cast<size_t>(count),
                hash);
        }
        if (!input.eof()) return false;
        p_fingerprint = hash;
        return true;
    }

    bool OpenRetainedFile(const std::string& p_path)
    {
#ifdef _MSC_VER
        m_fileHandle = CreateFileA(
            p_path.c_str(), GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_DELETE,
            nullptr, OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL, nullptr);
        return m_fileHandle !=
            INVALID_HANDLE_VALUE;
#else
        m_fileDescriptor =
            open(p_path.c_str(), O_RDONLY);
        return m_fileDescriptor >= 0;
#endif
    }

    bool RetainedFileSize(
        std::uint64_t& p_fileBytes) const
    {
#ifdef _MSC_VER
        std::lock_guard<std::mutex> lock(
            m_fileMutex);
        LARGE_INTEGER bytes {};
        if (m_fileHandle ==
                INVALID_HANDLE_VALUE ||
            GetFileSizeEx(
                m_fileHandle, &bytes) == 0 ||
            bytes.QuadPart < 0) {
            return false;
        }
        p_fileBytes =
            static_cast<std::uint64_t>(
                bytes.QuadPart);
        return true;
#else
        if (m_fileDescriptor < 0) return false;
        struct stat status {};
        if (fstat(m_fileDescriptor, &status) != 0 ||
            status.st_size < 0) {
            return false;
        }
        p_fileBytes =
            static_cast<std::uint64_t>(
                status.st_size);
        return true;
#endif
    }

    bool ReadRetained(
        std::uint64_t p_offset,
        void* p_buffer,
        size_t p_bytes) const
    {
        if (p_bytes == 0) return true;
#ifdef _MSC_VER
        if (p_offset >
            static_cast<std::uint64_t>(
                (std::numeric_limits<
                     LONGLONG>::max)())) {
            return false;
        }
        std::lock_guard<std::mutex> lock(
            m_fileMutex);
        LARGE_INTEGER offset {};
        offset.QuadPart =
            static_cast<LONGLONG>(p_offset);
        if (m_fileHandle ==
                INVALID_HANDLE_VALUE ||
            SetFilePointerEx(
                m_fileHandle, offset, nullptr,
                FILE_BEGIN) == 0) {
            return false;
        }
        auto* output =
            static_cast<std::uint8_t*>(p_buffer);
        size_t completed = 0;
        while (completed < p_bytes)
        {
            const DWORD requested =
                static_cast<DWORD>((std::min)(
                    p_bytes - completed,
                    static_cast<size_t>(
                        MAXDWORD)));
            DWORD count = 0;
            if (ReadFile(
                    m_fileHandle,
                    output + completed,
                    requested, &count,
                    nullptr) == 0 ||
                count == 0) {
                return false;
            }
            completed +=
                static_cast<size_t>(count);
        }
        return true;
#else
        if (m_fileDescriptor < 0 ||
            p_offset >
                static_cast<std::uint64_t>(
                    (std::numeric_limits<
                         off_t>::max)())) {
            return false;
        }
        auto* output =
            static_cast<std::uint8_t*>(p_buffer);
        size_t completed = 0;
        while (completed < p_bytes)
        {
            if (p_offset >
                    (std::numeric_limits<
                         std::uint64_t>::max)() -
                        completed ||
                p_offset + completed >
                    static_cast<std::uint64_t>(
                        (std::numeric_limits<
                             off_t>::max)())) {
                return false;
            }
            const size_t chunk =
                (std::min)(
                    p_bytes - completed,
                    static_cast<size_t>(
                        (std::numeric_limits<
                             ssize_t>::max)()));
            const ssize_t readBytes = pread(
                m_fileDescriptor,
                output + completed,
                chunk,
                static_cast<off_t>(
                    p_offset + completed));
            if (readBytes <= 0) return false;
            completed +=
                static_cast<size_t>(readBytes);
        }
        return true;
#endif
    }

    bool FingerprintRetained(
        Header p_header,
        std::uint64_t p_fileBytes,
        std::uint64_t& p_fingerprint) const
    {
        if (p_fileBytes < sizeof(Header)) {
            return false;
        }
        constexpr std::uint64_t kOffset =
            1469598103934665603ULL;
        std::uint64_t hash = kOffset;
        p_header.m_bodyFingerprint = 0;
        UpdateFingerprint(
            reinterpret_cast<const char*>(
                &p_header),
            sizeof(p_header), hash);
        std::vector<char> buffer(1 << 20);
        std::uint64_t offset = sizeof(Header);
        while (offset < p_fileBytes)
        {
            const size_t bytes =
                static_cast<size_t>(
                    (std::min<std::uint64_t>)(
                        buffer.size(),
                        p_fileBytes - offset));
            if (!ReadRetained(
                    offset, buffer.data(), bytes)) {
                return false;
            }
            UpdateFingerprint(
                buffer.data(), bytes, hash);
            offset += bytes;
        }
        p_fingerprint = hash;
        return true;
    }

    Header m_header;
    std::vector<DirectoryEntry> m_directory;
    std::string m_path;
#ifdef _MSC_VER
    mutable HANDLE m_fileHandle =
        INVALID_HANDLE_VALUE;
    mutable std::mutex m_fileMutex;
#else
    int m_fileDescriptor = -1;
#endif
};

} // namespace Cache
} // namespace SPTAG

#endif // _SPTAG_CACHE_EXTREMESPARSETAGSTORE_H_

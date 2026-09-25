// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/VectorIndex.h"
#include "inc/Helper/AtomicFile.h"
#include "inc/Helper/CommonHelper.h"
#include "inc/Helper/ConcurrentSet.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Helper/StringConvert.h"

#include "inc/Core/BKT/Index.h"
#include "inc/Core/KDT/Index.h"
#include "inc/Core/SPANN/Index.h"

#include <atomic>
#include <chrono>
#include <filesystem>
#include <cstring>
#include <limits>
#include <mutex>
#include <unordered_map>
#include <utility>

typedef typename SPTAG::Helper::Concurrent::ConcurrentMap<std::string, SPTAG::SizeType> MetadataMap;

namespace fs = std::filesystem;
using namespace SPTAG;

namespace {

thread_local VectorIndex::PostingScanStats g_threadLocalPostingScanStats{};
thread_local VectorIndex::ThreadLocalSearchContext g_threadLocalSearchContext{};
size_t AlignUp(size_t value, size_t alignment)
{
    return (value + alignment - 1) / alignment * alignment;
}

bool TryAlignUp(size_t value, size_t alignment, size_t& output)
{
    if (alignment == 0) return false;
    const size_t remainder = value % alignment;
    const size_t padding =
        remainder == 0 ? 0 : alignment - remainder;
    if (value >
        (std::numeric_limits<size_t>::max)() -
            padding) {
        return false;
    }
    output = value + padding;
    return true;
}

std::uint8_t* HeadNodeMetaBase(VectorIndex* index, SizeType sampleId)
{
    if (index == nullptr || sampleId < 0 || !index->HasHeadNodeMeta()) return nullptr;
    const size_t sid = static_cast<size_t>(sampleId);
    if (sid >= static_cast<size_t>(index->GetHeadNodeMetaSampleCount())) return nullptr;
    return index->GetHeadNodeMetaBlob().data() + sid * index->GetHeadNodeMetaStride();
}

const std::uint8_t* HeadNodeMetaBase(const VectorIndex* index, SizeType sampleId)
{
    if (index == nullptr || sampleId < 0 || !index->HasHeadNodeMeta()) return nullptr;
    const size_t sid = static_cast<size_t>(sampleId);
    if (sid >= static_cast<size_t>(index->GetHeadNodeMetaSampleCount())) return nullptr;
    return index->GetHeadNodeMetaBlob().data() + sid * index->GetHeadNodeMetaStride();
}

} // namespace

VectorIndex::ThreadLocalSearchContextGuard::ThreadLocalSearchContextGuard(ThreadLocalSearchContext p_context)
    : m_hadPrevious(g_threadLocalSearchContext.m_active),
      m_previous(g_threadLocalSearchContext)
{
    VectorIndex::SetThreadLocalSearchContext(std::move(p_context));
}

VectorIndex::ThreadLocalSearchContextGuard::~ThreadLocalSearchContextGuard()
{
    if (m_hadPrevious)
    {
        VectorIndex::SetThreadLocalSearchContext(std::move(m_previous));
    }
    else
    {
        VectorIndex::ResetThreadLocalSearchContext();
    }
}

Helper::LoggerHolder &SPTAG::GetLoggerHolder()
{
#ifdef DEBUG
    auto logLevel = Helper::LogLevel::LL_Debug;
#else
    auto logLevel = Helper::LogLevel::LL_Info;
#endif
#ifdef _WINDOWS_
    if (auto exeHandle = GetModuleHandleW(nullptr))
    {
        if (auto SPTAG_GetLoggerLevel =
                reinterpret_cast<SPTAG::Helper::LogLevel (*)()>(GetProcAddress(exeHandle, "SPTAG_GetLoggerLevel")))
        {
            logLevel = SPTAG_GetLoggerLevel();
        }
    }
#endif //  _WINDOWS_
    static Helper::LoggerHolder s_pLoggerHolder(std::make_shared<Helper::SimpleLogger>(logLevel));
    return s_pLoggerHolder;
}

std::shared_ptr<Helper::Logger> SPTAG::GetLogger()
{
    return GetLoggerHolder().GetLogger();
}

void SPTAG::SetLogger(std::shared_ptr<Helper::Logger> p_logger)
{
    GetLoggerHolder().SetLogger(p_logger);
}

std::shared_ptr<Helper::DiskIO> (*SPTAG::f_createIO)() = []() -> std::shared_ptr<Helper::DiskIO> {
    return std::shared_ptr<Helper::DiskIO>(new Helper::SimpleFileIO());
};

namespace SPTAG
{

bool copyfile(const char *oldpath, const char *newpath)
{
    auto input = f_createIO(), output = f_createIO();
    if (input == nullptr || !input->Initialize(oldpath, std::ios::binary | std::ios::in) || output == nullptr ||
        !output->Initialize(newpath, std::ios::binary | std::ios::out))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Unable to open files: %s %s\n", oldpath, newpath);
        return false;
    }

    const std::size_t bufferSize = 1 << 30;
    std::unique_ptr<char[]> bufferHolder(new char[bufferSize]);

    std::uint64_t readSize = input->ReadBinary(bufferSize, bufferHolder.get());
    while (readSize != 0)
    {
        if (output->WriteBinary(readSize, bufferHolder.get()) != readSize)
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Unable to write file: %s\n", newpath);
            return false;
        }
        readSize = input->ReadBinary(bufferSize, bufferHolder.get());
    }
    input->ShutDown();
    output->ShutDown();
    return true;
}

bool copydirectory(const fs::path &sourceDir, const fs::path &destinationDir,
                   bool p_linkFiles)
{
    try
    {
        // Ensure the destination directory exists.
        // create_directories will create parent directories if they don't exist.
        if (!fs::exists(destinationDir))
        {
            if (!fs::create_directories(destinationDir))
            {
                SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Error: Could not create destination directory %s\n",
                             destinationDir.string().c_str());
                return false;
            }
        }
        else if (!fs::is_directory(destinationDir))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Error: Destination path %s exists but is not a directory.\n",
                         destinationDir.string().c_str());
            return false;
        }

        // Iterate through all entries in the source directory
        for (const auto &entry : fs::directory_iterator(sourceDir))
        {
            fs::path currentPath = entry.path();
            fs::path destinationPath = destinationDir / currentPath.filename(); // Append filename to destination path

            if (fs::is_directory(currentPath))
            {
                // If it's a subdirectory, recursively copy its contents
                SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Copying directory: %s to %s\n", currentPath.string().c_str(),
                             destinationPath.string().c_str());
                if (entry.is_symlink() ||
                    !copydirectory(currentPath, destinationPath, p_linkFiles))
                {
                    return false; // Propagate error from recursive call
                }
            }
            else
            {
                // If it's a file, copy it directly
                SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Copying file: %s to %s\n", currentPath.string().c_str(),
                             destinationPath.string().c_str());
                if (!entry.is_regular_file()) return false;
                if (p_linkFiles)
                    fs::create_hard_link(fs::canonical(currentPath), destinationPath);
                else
                    fs::copy_file(currentPath, destinationPath, fs::copy_options::overwrite_existing);
            }
        }
    }
    catch (const fs::filesystem_error &e)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Filesystem error: %s\n", e.what());
        return false;
    }
    catch (const std::exception &e)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "General error: %s\n", e.what());
        return false;
    }
    return true;
}

bool copydirectory(const fs::path &sourceDir, const fs::path &destinationDir)
{
    return copydirectory(sourceDir, destinationDir, false);
}

#ifndef _MSC_VER
void listdir(std::string path, std::vector<std::string> &files)
{
    if (auto dirptr = opendir(path.substr(0, path.length() - 1).c_str()))
    {
        while (auto f = readdir(dirptr))
        {
            if (!f->d_name || f->d_name[0] == '.')
                continue;
            std::string tmp = path.substr(0, path.length() - 1);
            tmp += std::string(f->d_name);
            if (f->d_type == DT_DIR)
            {
                listdir(tmp + FolderSep + "*", files);
            }
            else
            {
                files.push_back(tmp);
            }
        }
        closedir(dirptr);
    }
}
#else
void listdir(std::string path, std::vector<std::string> &files)
{
    WIN32_FIND_DATA fd;
    HANDLE hFile = FindFirstFile(path.c_str(), &fd);
    if (hFile != INVALID_HANDLE_VALUE)
    {
        do
        {
            std::string tmp = path.substr(0, path.length() - 1);
            tmp += std::string(fd.cFileName);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            {
                if (fd.cFileName[0] != '.')
                {
                    listdir(tmp + FolderSep + "*", files);
                }
            }
            else
            {
                files.push_back(tmp);
            }
        } while (FindNextFile(hFile, &fd));
        FindClose(hFile);
    }
}
#endif
} // namespace SPTAG

VectorIndex::VectorIndex()
{
}

VectorIndex::~VectorIndex()
{
}

void VectorIndex::ClearHeadNodeMeta()
{
    ++m_headNodeMetaRevision;
    m_headLayout.stride = 0;
    m_headLayout.pure = 0;
    m_headLayout.tail = 0;
    m_headLayout.hasTail = false;
    m_headLayout.own = 0;
    m_headLayout.legacyPosting = 0;
    m_headLayout.vid = 0;
    m_headLayout.bundle = 0;
    m_headLayout.flags = 0;
    m_headLayout.numeric = 0;
    m_headLayout.tailNumeric = 0;
    m_headLayout.numericColumns = 0;
    m_headNodeNumericDomainFingerprint = 0;
    m_headNodeOwnTagsAvailable = false;
    m_headNodePostingHierMasksAvailable = false;
    m_headLayout = HeadMetadataLayout{};
    std::vector<std::uint8_t>().swap(m_headNodeMeta);
}

void VectorIndex::InitializeCompactHeadNodeMeta(
    SizeType count, const TagSchema& schema, const Cache::HierWidthTable& widths, bool tails)
{
    const auto layout = HeadMetadataLayout::Compact(schema, widths, tails);
    if (count <= 0 || static_cast<std::size_t>(count) >
        (std::numeric_limits<std::size_t>::max)() / layout.stride)
        throw std::invalid_argument("Invalid compact metadata sample count");
    ClearHeadNodeMeta();
    m_headLayout = layout;
    m_headLayout.count = count;
    m_headNodeMeta.assign(static_cast<std::size_t>(count) * layout.stride, 0);
    for (SizeType head = 0; head < count; ++head) {
        SetHeadNodeGlobalVID(head, MaxSize);
        SetHeadNodeBundleNodeId(head, -1);
    }
}

bool VectorIndex::TryComputeHeadNodeMetaStride(
    int p_numQuantCols, size_t& p_stride)
{
    return TryComputeHeadNodeMetaStride(
        p_numQuantCols, Cache::HierWidths(),
        false, p_stride);
}

bool VectorIndex::TryComputeHeadNodeMetaStride(
    int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths,
    size_t& p_stride)
{
    return TryComputeHeadNodeMetaStride(
        p_numQuantCols, p_hierWidths,
        false, p_stride);
}

bool VectorIndex::TryComputeHeadNodeMetaStride(
    int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths,
    bool p_includeTailPS,
    size_t& p_stride)
{
    p_stride = 0;
    if (p_numQuantCols < 0) return false;

    size_t offset =
        sizeof(Cache::PostingBitmask);
    if (p_includeTailPS) {
        if (!TryAlignUp(
                offset,
                alignof(Cache::PostingBitmask),
                offset) ||
            offset >
                (std::numeric_limits<size_t>::max)() -
                    sizeof(Cache::PostingBitmask)) {
            return false;
        }
        offset += sizeof(Cache::PostingBitmask);
    }
    if (!TryAlignUp(
            offset,
            alignof(Cache::HierarchicalOwnTags),
            offset) ||
        offset >
            (std::numeric_limits<size_t>::max)() -
                sizeof(Cache::HierarchicalOwnTags)) {
        return false;
    }
    offset += sizeof(Cache::HierarchicalOwnTags);
    if (!TryAlignUp(
            offset,
            alignof(Cache::HierarchicalPostingMask),
            offset) ||
        offset >
            (std::numeric_limits<size_t>::max)() -
                Cache::HierPostingMaskBytes(
                    p_hierWidths)) {
        return false;
    }
    offset += Cache::HierPostingMaskBytes(
        p_hierWidths);
    if (!TryAlignUp(
            offset, alignof(SizeType), offset) ||
        offset >
            (std::numeric_limits<size_t>::max)() -
                sizeof(SizeType)) {
        return false;
    }
    offset += sizeof(SizeType);
    if (!TryAlignUp(
            offset, alignof(int16_t), offset) ||
        offset >
            (std::numeric_limits<size_t>::max)() -
                sizeof(int16_t)) {
        return false;
    }
    offset += sizeof(int16_t);
    if (!TryAlignUp(
            offset, alignof(std::uint8_t), offset) ||
        offset >
            (std::numeric_limits<size_t>::max)() -
                sizeof(std::uint8_t)) {
        return false;
    }
    offset += sizeof(std::uint8_t);

    if (p_numQuantCols > 0) {
        if (!TryAlignUp(
                offset,
                alignof(std::uint64_t),
                offset)) {
            return false;
        }
        constexpr size_t bytesPerColumn =
            static_cast<size_t>(
                Cache::NUM_QUANT_WORDS) *
            sizeof(std::uint64_t);
        const size_t columns =
            static_cast<size_t>(p_numQuantCols);
        if (columns >
            (std::numeric_limits<size_t>::max)() /
                bytesPerColumn) {
            return false;
        }
        const size_t numericBytes =
            columns * bytesPerColumn;
        if (offset >
            (std::numeric_limits<size_t>::max)() -
                numericBytes) {
            return false;
        }
        offset += numericBytes;
        if (p_includeTailPS) {
            if (!TryAlignUp(
                    offset,
                    alignof(std::uint64_t),
                    offset) ||
                offset >
                    (std::numeric_limits<size_t>::max)() -
                        numericBytes) {
                return false;
            }
            offset += numericBytes;
        }
    }
    return TryAlignUp(
        offset,
        alignof(Cache::PostingBitmask),
        p_stride);
}

void VectorIndex::InitializeHeadNodeMeta(
    SizeType p_numSamples, int p_numQuantCols)
{
    const Cache::HierWidthTable legacyWidths =
        Cache::HierWidths();
    InitializeHeadNodeMeta(
        p_numSamples, p_numQuantCols,
        legacyWidths);
}

void VectorIndex::InitializeHeadNodeMeta(
    SizeType p_numSamples, int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths)
{
    InitializeHeadNodeMeta(
        p_numSamples, p_numQuantCols,
        p_hierWidths, false);
}

void VectorIndex::InitializeHeadNodeMeta(
    SizeType p_numSamples, int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths,
    bool p_includeTailPS)
{
    if (!InitializeHeadNodeMetaLayout(
            p_numSamples, p_numQuantCols, p_hierWidths, p_includeTailPS))
        return;
    m_headNodeMeta.assign(static_cast<size_t>(p_numSamples) * m_headLayout.stride, 0);
    for (SizeType sampleId = 0; sampleId < p_numSamples; ++sampleId) {
        SetHeadNodeGlobalVID(sampleId, MaxSize);
        SetHeadNodeBundleNodeId(sampleId, -1);
    }
}

bool VectorIndex::AdoptHeadNodeMeta(
    SizeType p_numSamples, int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths,
    bool p_includeTailPS, std::vector<std::uint8_t>&& p_blob)
{
    size_t stride = 0;
    if (&p_blob == &m_headNodeMeta || p_numSamples <= 0 ||
        !TryComputeHeadNodeMetaStride(
            p_numQuantCols, p_hierWidths, p_includeTailPS, stride) ||
        static_cast<size_t>(p_numSamples) > (std::numeric_limits<size_t>::max)() / stride ||
        p_blob.size() != static_cast<size_t>(p_numSamples) * stride)
        return false;
    if (!InitializeHeadNodeMetaLayout(
            p_numSamples, p_numQuantCols, p_hierWidths, p_includeTailPS))
        return false;
    m_headNodeMeta = std::move(p_blob);
    return true;
}

bool VectorIndex::InitializeHeadNodeMetaLayout(
    SizeType p_numSamples, int p_numQuantCols,
    const Cache::HierWidthTable& p_hierWidths,
    bool p_includeTailPS)
{
    ClearHeadNodeMeta();
    if (p_numSamples <= 0) return false;
    m_headLayout.widths = p_hierWidths;
    const int quantCols =
        p_numQuantCols > 0 ? p_numQuantCols : 0;
    size_t computedStride = 0;
    if (!TryComputeHeadNodeMetaStride(
            quantCols, p_hierWidths,
            p_includeTailPS,
            computedStride) ||
        static_cast<size_t>(p_numSamples) >
            (std::numeric_limits<size_t>::max)() /
                computedStride) {
        return false;
    }

    // V3 layout per sample:
    // | pure PostingBitmask | HierarchicalOwnTags own-tags | HierarchicalPostingMask posting-content | globalVID (4B) | bundleNodeId (2B) | headOnly (1B) |
    // V4 appends, after headOnly, a quantized numeric block of M*NUM_QUANT_WORDS
    // uint64 (one 256-bit lane per numeric column). With M==0 the stride is
    // byte-identical to V3, so V3 indexes load unchanged.
    // V6 inserts a second PostingBitmask for the self-contained global tail.
    m_headLayout.pure = 0;
    m_headLayout.hasTail = p_includeTailPS;
    size_t afterPostingPS =
        m_headLayout.pure +
        sizeof(Cache::PostingBitmask);
    if (m_headLayout.hasTail) {
        m_headLayout.tail = AlignUp(
            afterPostingPS,
            alignof(Cache::PostingBitmask));
        afterPostingPS =
            m_headLayout.tail +
            sizeof(Cache::PostingBitmask);
    } else {
        m_headLayout.tail = 0;
    }
    m_headLayout.own = AlignUp(afterPostingPS, alignof(Cache::HierarchicalOwnTags));
    m_headLayout.legacyPosting = AlignUp(m_headLayout.own + sizeof(Cache::HierarchicalOwnTags), alignof(Cache::HierarchicalPostingMask));
    // Posting-content masks use this index's persisted width packing rather
    // than process-global state, so separately loaded tenants cannot alter the
    // record layout.
    m_headLayout.vid = AlignUp(
        m_headLayout.legacyPosting +
            Cache::HierPostingMaskBytes(
                p_hierWidths),
        alignof(SizeType));
    m_headLayout.bundle = AlignUp(m_headLayout.vid + sizeof(SizeType), alignof(int16_t));
    m_headLayout.flags = AlignUp(m_headLayout.bundle + sizeof(int16_t), alignof(std::uint8_t));
    m_headLayout.numericColumns = quantCols;
    size_t afterHeadOnly = m_headLayout.flags + sizeof(std::uint8_t);
    if (m_headLayout.numericColumns > 0) {
        m_headLayout.numeric = AlignUp(afterHeadOnly, alignof(std::uint64_t));
        afterHeadOnly = m_headLayout.numeric +
            (size_t)m_headLayout.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
        if (m_headLayout.hasTail) {
            m_headLayout.tailNumeric =
                AlignUp(
                    afterHeadOnly,
                    alignof(std::uint64_t));
            afterHeadOnly =
                m_headLayout.tailNumeric +
                (size_t)m_headLayout.numericColumns *
                    Cache::NUM_QUANT_WORDS *
                    sizeof(std::uint64_t);
        } else {
            m_headLayout.tailNumeric = 0;
        }
    } else {
        m_headLayout.numeric = 0;
        m_headLayout.tailNumeric = 0;
    }
    m_headLayout.stride = computedStride;
    m_headLayout.count = p_numSamples;
    for (int column = 0; column < Cache::HIER_LEVELS; ++column)
        m_headLayout.categorical[column] = m_headLayout.legacyPosting +
            p_hierWidths.wordOff[column] * sizeof(std::uint64_t);
    return true;
}

std::uint64_t* VectorIndex::GetHeadNodeNumQuantMutable(SizeType p_sampleId)
{
    ++m_headNodeMetaRevision;
    if (p_sampleId < 0 || p_sampleId >= m_headLayout.count) return nullptr;
    if (m_headLayout.numericColumns <= 0 || m_headLayout.stride == 0) return nullptr;
    size_t base = static_cast<size_t>(p_sampleId) * m_headLayout.stride + m_headLayout.numeric;
    size_t bytes = (size_t)m_headLayout.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
    if (base + bytes > m_headNodeMeta.size()) return nullptr;
    return reinterpret_cast<std::uint64_t*>(m_headNodeMeta.data() + base);
}

const std::uint64_t* VectorIndex::GetHeadNodeNumQuant(SizeType p_sampleId) const
{
    if (p_sampleId < 0 || p_sampleId >= m_headLayout.count) return nullptr;
    if (m_headLayout.numericColumns <= 0 || m_headLayout.stride == 0) return nullptr;
    size_t base = static_cast<size_t>(p_sampleId) * m_headLayout.stride + m_headLayout.numeric;
    size_t bytes = (size_t)m_headLayout.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
    if (base + bytes > m_headNodeMeta.size()) return nullptr;
    return reinterpret_cast<const std::uint64_t*>(m_headNodeMeta.data() + base);
}

std::uint64_t*
VectorIndex::GetHeadNodeTailNumQuantMutable(
    SizeType p_sampleId)
{
    ++m_headNodeMetaRevision;
    if (p_sampleId < 0 || p_sampleId >= m_headLayout.count) return nullptr;
    if (!m_headLayout.hasTail ||
        m_headLayout.numericColumns <= 0 ||
        m_headLayout.stride == 0) {
        return nullptr;
    }
    const size_t base =
        static_cast<size_t>(p_sampleId) *
            m_headLayout.stride +
        m_headLayout.tailNumeric;
    const size_t bytes =
        static_cast<size_t>(
            m_headLayout.numericColumns) *
        Cache::NUM_QUANT_WORDS *
        sizeof(std::uint64_t);
    if (base + bytes >
        m_headNodeMeta.size()) {
        return nullptr;
    }
    return reinterpret_cast<std::uint64_t*>(
        m_headNodeMeta.data() + base);
}

const std::uint64_t*
VectorIndex::GetHeadNodeTailNumQuant(
    SizeType p_sampleId) const
{
    if (p_sampleId < 0 || p_sampleId >= m_headLayout.count) return nullptr;
    if (!m_headLayout.hasTail ||
        m_headLayout.numericColumns <= 0 ||
        m_headLayout.stride == 0) {
        return nullptr;
    }
    const size_t base =
        static_cast<size_t>(p_sampleId) *
            m_headLayout.stride +
        m_headLayout.tailNumeric;
    const size_t bytes =
        static_cast<size_t>(
            m_headLayout.numericColumns) *
        Cache::NUM_QUANT_WORDS *
        sizeof(std::uint64_t);
    if (base + bytes >
        m_headNodeMeta.size()) {
        return nullptr;
    }
    return reinterpret_cast<
        const std::uint64_t*>(
            m_headNodeMeta.data() + base);
}

SizeType VectorIndex::GetHeadNodeMetaSampleCount() const
{
    if (m_headLayout.stride == 0) return 0;
    return static_cast<SizeType>(m_headNodeMeta.size() / m_headLayout.stride);
}

void VectorIndex::SetHeadNodeGlobalVID(SizeType p_sampleId, SizeType p_globalVID)
{
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    std::memcpy(base + m_headLayout.vid, &p_globalVID, sizeof(SizeType));
}

SizeType VectorIndex::GetHeadNodeGlobalVID(SizeType p_sampleId) const
{
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return MaxSize;
    SizeType globalVID = MaxSize;
    std::memcpy(&globalVID, base + m_headLayout.vid, sizeof(SizeType));
    return globalVID;
}

void VectorIndex::SetHeadNodePS(SizeType p_sampleId, const Cache::PostingBitmask& p_ps)
{
    ++m_headNodeMetaRevision;
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    if (m_headLayout.pure == HeadMetadataLayout::Absent) {
        if (p_ps.Popcount() != 0) throw std::invalid_argument("Categorical signature in a numeric-only layout");
        return;
    }
    std::memcpy(base + m_headLayout.pure, &p_ps, sizeof(Cache::PostingBitmask));
}

const Cache::PostingBitmask* VectorIndex::GetHeadNodePS(SizeType p_sampleId) const
{
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return nullptr;
    static const Cache::PostingBitmask empty;
    if (m_headLayout.pure == HeadMetadataLayout::Absent) return &empty;
    return reinterpret_cast<const Cache::PostingBitmask*>(base + m_headLayout.pure);
}

bool VectorIndex::HeadNodePSMayIntersect(SizeType p_sampleId, const Cache::PostingBitmask& p_queryMask) const
{
    const auto* ps = GetHeadNodePS(p_sampleId);
    return ps != nullptr && ps->MayIntersect(p_queryMask);
}

void VectorIndex::SetHeadNodeTailPS(
    SizeType p_sampleId,
    const Cache::PostingBitmask& p_ps)
{
    ++m_headNodeMetaRevision;
    if (!m_headLayout.hasTail) return;
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    if (m_headLayout.tail == HeadMetadataLayout::Absent) {
        if (p_ps.Popcount() != 0) throw std::invalid_argument("Categorical tail signature in a numeric-only layout");
        return;
    }
    std::memcpy(
        base + m_headLayout.tail,
        &p_ps, sizeof(Cache::PostingBitmask));
}

const Cache::PostingBitmask*
VectorIndex::GetHeadNodeTailPS(
    SizeType p_sampleId) const
{
    if (!m_headLayout.hasTail) return nullptr;
    const auto* base = HeadNodeMetaBase(
        this, p_sampleId);
    if (base == nullptr) return nullptr;
    static const Cache::PostingBitmask empty;
    if (m_headLayout.tail == HeadMetadataLayout::Absent) return &empty;
    return reinterpret_cast<
        const Cache::PostingBitmask*>(
            base + m_headLayout.tail);
}

bool VectorIndex::HeadNodeTailPSMayIntersect(
    SizeType p_sampleId,
    const Cache::PostingBitmask& p_queryMask) const
{
    const auto* ps = GetHeadNodeTailPS(
        p_sampleId);
    return ps != nullptr &&
        ps->MayIntersect(p_queryMask);
}

void VectorIndex::SetHeadNodeHeadOnly(SizeType p_sampleId, bool p_isHeadOnly)
{
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    base[m_headLayout.flags] = p_isHeadOnly ? 1 : 0;
}

bool VectorIndex::IsHeadNodeHeadOnly(SizeType p_sampleId) const
{
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    return base != nullptr && base[m_headLayout.flags] != 0;
}

void VectorIndex::SetHeadNodeHierMask(SizeType p_sampleId, const Cache::HierarchicalOwnTags& p_mask)
{
    ++m_headNodeMetaRevision;
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    std::memcpy(base + m_headLayout.own, p_mask.tag,
        m_headLayout.ownColumns * sizeof(std::uint32_t));
    m_headNodeOwnTagsAvailable = true;
}

HeadOwnTagsView VectorIndex::GetHeadNodeHierMask(SizeType p_sampleId) const
{
    if (!m_headNodeOwnTagsAvailable) return {};
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return {};
    return {reinterpret_cast<const std::uint32_t*>(base + m_headLayout.own),
        m_headLayout.ownColumns};
}

void VectorIndex::SetHeadNodePostingHierMask(SizeType p_sampleId, const Cache::HierarchicalPostingMask& p_mask)
{
    ++m_headNodeMetaRevision;
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    for (int column = 0; column < Cache::HIER_LEVELS; ++column) {
        const auto offset = m_headLayout.categorical[column];
        const auto* words = p_mask.mask + m_headLayout.widths.wordOff[column];
        const auto bytes = m_headLayout.widths.bits[column] / 8;
        if (offset != HeadMetadataLayout::Absent)
            std::memcpy(base + offset, words, bytes);
        else
            for (int word = 0; word < bytes / 8; ++word)
                if (words[word] != 0)
                    throw std::invalid_argument("Categorical mask in a noncategorical compact column");
    }
    m_headNodePostingHierMasksAvailable = true;
}

HeadPostingMaskView VectorIndex::GetHeadNodePostingHierMask(SizeType p_sampleId) const
{
    if (!m_headNodePostingHierMasksAvailable) return {};
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    return {base, &m_headLayout};
}

void VectorIndex::SetHeadNodeBundleNodeId(SizeType p_sampleId, int16_t p_bundleNodeId)
{
    auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return;
    std::memcpy(base + m_headLayout.bundle, &p_bundleNodeId, sizeof(int16_t));
}

int16_t VectorIndex::GetHeadNodeBundleNodeId(SizeType p_sampleId) const
{
    const auto* base = HeadNodeMetaBase(this, p_sampleId);
    if (base == nullptr) return -1;
    int16_t bundleNodeId = -1;
    std::memcpy(&bundleNodeId, base + m_headLayout.bundle, sizeof(int16_t));
    return bundleNodeId;
}

Cache::HierWidthTable
VectorIndex::GetHeadNodeHierWidths() const
{
    return m_headLayout.widths;
}

bool VectorIndex::HeadNodeMatchesQuery(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask) const
{
    return HeadNodeMatchesQuery(
        p_sampleId, p_queryMask,
        GetHeadNodeHierWidths());
}

bool VectorIndex::HeadNodeMatchesQuery(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask,
    const Cache::HierWidthTable& p_hierWidths) const
{
    // Only consider heads that are head-only (have their own tags)
    if (!IsHeadNodeHeadOnly(p_sampleId)) return false;

    // Check hierarchical mask intersection
    const auto hierMask = GetHeadNodeHierMask(p_sampleId);
    if (hierMask == nullptr) return false;
    return hierMask->MayIntersect(
        p_queryMask, p_hierWidths);
}

bool VectorIndex::HeadHierMaskMayIntersect(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask) const
{
    return HeadHierMaskMayIntersect(
        p_sampleId, p_queryMask,
        GetHeadNodeHierWidths());
}

bool VectorIndex::HeadHierMaskMayIntersect(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask,
    const Cache::HierWidthTable& p_hierWidths) const
{
    const auto hierMask = GetHeadNodeHierMask(p_sampleId);
    if (hierMask == nullptr) return false;
    return hierMask->MayIntersect(
        p_queryMask, p_hierWidths);
}

bool VectorIndex::HeadPostingHierMaskMayIntersect(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask) const
{
    return HeadPostingHierMaskMayIntersect(
        p_sampleId, p_queryMask,
        GetHeadNodeHierWidths());
}

bool VectorIndex::HeadPostingHierMaskMayIntersect(
    SizeType p_sampleId,
    const Cache::HierarchicalPostingMask& p_queryMask,
    const Cache::HierWidthTable& p_hierWidths) const
{
    const auto postingMask = GetHeadNodePostingHierMask(p_sampleId);
    // Fail-open: legacy / V2 indexes lack the posting-union mask. Keep head
    // so the caller doesn't drop posting candidates spuriously.
    if (postingMask == nullptr) return true;
    return postingMask->MayIntersect(
        p_queryMask, p_hierWidths);
}

void VectorIndex::ResetThreadLocalPostingScanStats()
{
    g_threadLocalPostingScanStats = PostingScanStats{};
}

void VectorIndex::SetThreadLocalPostingScanStats(uint64_t p_readPostings, uint64_t p_matchedPostings,
                                                 uint64_t p_prePSPostings,
                                                 uint64_t p_scannedVectors,
                                                 uint64_t p_matchedVectors,
                                                 uint64_t p_postingPageReads,
                                                 uint64_t p_postingLogicalBytes,
                                                 uint64_t p_postingPhysicalBytes,
                                                 uint64_t p_adcScannedVectors,
                                                 uint64_t p_adcSurvivors,
                                                 uint64_t p_rerankCandidates,
                                                 uint64_t p_rerankReadRequests,
                                                 uint64_t p_rerankPhysicalBytes,
                                                 uint64_t p_uniqueMatchedPostings,
                                                 uint64_t p_uniqueMatchedVectors,
                                                 uint64_t p_dedupSkippedVectors)
{
    g_threadLocalPostingScanStats.m_readPostings = p_readPostings;
    g_threadLocalPostingScanStats.m_matchedPostings = p_matchedPostings;
    g_threadLocalPostingScanStats.m_prePSPostings = p_prePSPostings;
    g_threadLocalPostingScanStats.m_scannedVectors = p_scannedVectors;
    g_threadLocalPostingScanStats.m_matchedVectors = p_matchedVectors;
    g_threadLocalPostingScanStats.m_dedupSkippedVectors = p_dedupSkippedVectors;
    g_threadLocalPostingScanStats.m_uniqueMatchedPostings = p_uniqueMatchedPostings;
    g_threadLocalPostingScanStats.m_uniqueMatchedVectors = p_uniqueMatchedVectors;
    g_threadLocalPostingScanStats.m_postingPageReads = p_postingPageReads;
    g_threadLocalPostingScanStats.m_postingLogicalBytes = p_postingLogicalBytes;
    g_threadLocalPostingScanStats.m_postingPhysicalBytes = p_postingPhysicalBytes;
    g_threadLocalPostingScanStats.m_adcScannedVectors = p_adcScannedVectors;
    g_threadLocalPostingScanStats.m_adcSurvivors = p_adcSurvivors;
    g_threadLocalPostingScanStats.m_rerankCandidates = p_rerankCandidates;
    g_threadLocalPostingScanStats.m_rerankReadRequests = p_rerankReadRequests;
    g_threadLocalPostingScanStats.m_rerankPhysicalBytes = p_rerankPhysicalBytes;
}

VectorIndex::PostingScanStats VectorIndex::GetThreadLocalPostingScanStats()
{
    return g_threadLocalPostingScanStats;
}

void VectorIndex::SetThreadLocalSearchContext(ThreadLocalSearchContext p_context)
{
    p_context.m_active = true;
    g_threadLocalSearchContext = std::move(p_context);
}

void VectorIndex::ResetThreadLocalSearchContext()
{
    g_threadLocalSearchContext.Reset();
}

const VectorIndex::ThreadLocalSearchContext* VectorIndex::GetThreadLocalSearchContext()
{
    return g_threadLocalSearchContext.m_active ? &g_threadLocalSearchContext : nullptr;
}

std::string VectorIndex::GetParameter(const std::string &p_param, const std::string &p_section) const
{
    return GetParameter(p_param.c_str(), p_section.c_str());
}

ErrorCode VectorIndex::SetParameter(const std::string &p_param, const std::string &p_value,
                                    const std::string &p_section)
{
    return SetParameter(p_param.c_str(), p_value.c_str(), p_section.c_str());
}

void VectorIndex::SetMetadata(MetadataSet *p_new)
{
    m_pMetadata.reset(p_new);
}

MetadataSet *VectorIndex::GetMetadata() const
{
    return m_pMetadata.get();
}

ByteArray VectorIndex::GetMetadata(SizeType p_vectorID) const
{
    if (nullptr != m_pMetadata)
    {
        return m_pMetadata->GetMetadata(p_vectorID);
    }
    return ByteArray::c_empty;
}

std::shared_ptr<std::vector<std::uint64_t>> VectorIndex::CalculateBufferSize() const
{
    std::shared_ptr<std::vector<std::uint64_t>> ret = BufferSize();

    if (m_pMetadata != nullptr)
    {
        auto metasize = m_pMetadata->BufferSize();
        ret->push_back(metasize.first);
        ret->push_back(metasize.second);
    }

    if (m_pQuantizer)
    {
        ret->push_back(m_pQuantizer->BufferSize());
    }
    return std::move(ret);
}

ErrorCode VectorIndex::LoadIndexConfig(Helper::IniReader &p_reader)
{
    std::string metadataSection("MetaData");
    if (p_reader.DoesSectionExist(metadataSection))
    {
        m_sMetadataFile = p_reader.GetParameter(metadataSection, "MetaDataFilePath", std::string());
        m_sMetadataIndexFile = p_reader.GetParameter(metadataSection, "MetaDataIndexPath", std::string());
    }

    std::string quantizerSection("Quantizer");
    if (p_reader.DoesSectionExist(quantizerSection))
    {
        m_sQuantizerFile = p_reader.GetParameter(quantizerSection, "QuantizerFilePath", std::string());
    }
    return LoadConfig(p_reader);
}

ErrorCode VectorIndex::SaveIndexConfig(std::shared_ptr<Helper::DiskIO> p_configOut)
{
    if (nullptr != m_pMetadata)
    {
        IOSTRING(p_configOut, WriteString, "[MetaData]\n");
        IOSTRING(p_configOut, WriteString, ("MetaDataFilePath=" + m_sMetadataFile + "\n").c_str());
        IOSTRING(p_configOut, WriteString, ("MetaDataIndexPath=" + m_sMetadataIndexFile + "\n").c_str());
        if (nullptr != m_pMetaToVec)
            IOSTRING(p_configOut, WriteString, "MetaDataToVectorIndex=true\n");
        IOSTRING(p_configOut, WriteString, "\n");
    }

    if (m_pQuantizer)
    {
        IOSTRING(p_configOut, WriteString, "[Quantizer]\n");
        IOSTRING(p_configOut, WriteString, ("QuantizerFilePath=" + m_sQuantizerFile + "\n").c_str());
        IOSTRING(p_configOut, WriteString, "\n");
    }

    IOSTRING(p_configOut, WriteString, "[Index]\n");
    IOSTRING(p_configOut, WriteString,
             ("IndexAlgoType=" + Helper::Convert::ConvertToString(GetIndexAlgoType()) + "\n").c_str());
    IOSTRING(p_configOut, WriteString,
             ("ValueType=" + Helper::Convert::ConvertToString(GetVectorValueType()) + "\n").c_str());
    IOSTRING(p_configOut, WriteString, "\n");

    return SaveConfig(p_configOut);
}

SizeType VectorIndex::GetMetaMapping(std::string &meta) const
{
    MetadataMap *ptr = static_cast<MetadataMap *>(m_pMetaToVec.get());
    auto iter = ptr->find(meta);
    if (iter != ptr->end())
        return iter->second;
    return -1;
}

void VectorIndex::UpdateMetaMapping(const std::string &meta, SizeType i)
{
    MetadataMap *ptr = static_cast<MetadataMap *>(m_pMetaToVec.get());
    auto iter = ptr->find(meta);
    if (iter != ptr->end())
        DeleteIndex(iter->second);
    ;
    (*ptr)[meta] = i;
}

void VectorIndex::BuildMetaMapping(bool p_checkDeleted)
{
    MetadataMap *ptr = new MetadataMap(m_iDataBlockSize);
    for (SizeType i = 0; i < m_pMetadata->Count(); i++)
    {
        if (!p_checkDeleted || ContainSample(i))
        {
            ByteArray meta = m_pMetadata->GetMetadata(i);
            (*ptr)[std::string((char *)meta.Data(), meta.Length())] = i;
        }
    }
    m_pMetaToVec.reset(ptr, std::default_delete<MetadataMap>());
}

ErrorCode VectorIndex::SaveIndex(std::string &p_config, const std::vector<ByteArray> &p_indexBlobs)
{
    if (!m_bReady || GetNumSamples() - GetNumDeleted() == 0)
        return ErrorCode::EmptyIndex;
    if (!SupportsNonDirectorySave())
    {
        SPTAGLIB_LOG(
            Helper::LogLevel::LL_Error,
            "Mutable limited-tag H/O indexes support directory SaveIndex only.\n");
        return ErrorCode::Undefined;
    }

    ErrorCode ret = ErrorCode::Success;
    {
        std::shared_ptr<Helper::DiskIO> p_configStream(new Helper::SimpleBufferIO());
        auto bufsize = 2 << 20;
        std::vector<char> buf(bufsize); // Allocate 1 MB scratch space
        if (p_configStream == nullptr || !p_configStream->Initialize(buf.data(), std::ios::out, bufsize))
            return ErrorCode::EmptyDiskIO;
        if ((ret = SaveIndexConfig(p_configStream)) != ErrorCode::Success)
            return ret;
        p_config.resize(p_configStream->TellP());
        IOBINARY(p_configStream, ReadBinary, p_config.size(), (char *)p_config.c_str(), 0);
    }

    std::vector<std::shared_ptr<Helper::DiskIO>> p_indexStreams;
    for (size_t i = 0; i < p_indexBlobs.size(); i++)
    {
        std::shared_ptr<Helper::DiskIO> ptr(new Helper::SimpleBufferIO());
        if (ptr == nullptr || !ptr->Initialize((char *)p_indexBlobs[i].Data(), std::ios::binary | std::ios::out,
                                               p_indexBlobs[i].Length()))
            return ErrorCode::EmptyDiskIO;
        p_indexStreams.push_back(std::move(ptr));
    }

    size_t metaStart = BufferSize()->size();
    if (NeedRefine())
    {
        ret = RefineIndex(p_indexStreams, nullptr, nullptr);
    }
    else
    {
        if (m_pMetadata != nullptr && p_indexStreams.size() >= metaStart + 2)
        {

            ret = m_pMetadata->SaveMetadata(p_indexStreams[metaStart], p_indexStreams[metaStart + 1]);
        }
        if (ErrorCode::Success == ret)
            ret = SaveIndexData(p_indexStreams);
    }
    if (m_pMetadata != nullptr)
        metaStart += 2;

    if (ErrorCode::Success == ret && m_pQuantizer && p_indexStreams.size() > metaStart)
    {
        ret = m_pQuantizer->SaveQuantizer(p_indexStreams[metaStart]);
    }
    return ret;
}

ErrorCode VectorIndex::SaveIndex(const std::string &p_folderPath)
{
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "SaveIndex(%s) begin...ready:%d GetNumSamples():%d GetNumDeleted():%d\n",
                 p_folderPath.c_str(), m_bReady, GetNumSamples(), GetNumDeleted());
    if (!m_bReady || GetNumSamples() - GetNumDeleted() == 0)
        return ErrorCode::EmptyIndex;
    const std::string configuredFolder =
        GetParameter("IndexDirectory", "Base");
    std::error_code pathError;
    const bool sameFolder =
        configuredFolder == p_folderPath ||
        fs::equivalent(
            configuredFolder, p_folderPath,
            pathError);
    auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(this);
    if (spann != nullptr &&
        spann->GetOptions()->m_storage == Storage::STATIC &&
        spann->GetOptions()->m_selectSecondLevel &&
        !spann->GetOptions()->m_buildH1Graph)
    {
        // A graphless root is a compatibility KDT, not a head bundle. Its
        // catalog, upper graph and generation-bound CSR files are already
        // persisted; serializing it as a bundle can damage that generation.
        fs::path staging;
        const auto cleanup = [&]()
        {
            SetParameter("IndexDirectory", configuredFolder, "Base");
            if (!staging.empty())
            {
                std::error_code error;
                fs::remove_all(staging, error);
            }
        };
        try
        {
            if (configuredFolder.empty() || p_folderPath.empty())
                return ErrorCode::FailedCreateFile;
            const fs::path source = fs::canonical(configuredFolder);
            const fs::path destination = fs::weakly_canonical(fs::absolute(p_folderPath));
            const auto isWithin = [](const fs::path& child, const fs::path& parent)
            {
                auto childPart = child.begin();
                for (auto parentPart = parent.begin(); parentPart != parent.end();
                     ++parentPart, ++childPart)
                {
                    if (childPart == child.end() || *childPart != *parentPart)
                        return false;
                }
                return true;
            };
            if (!sameFolder &&
                (isWithin(destination, source) || isWithin(source, destination)))
            {
                SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                             "Index export source and destination must not overlap.\n");
                return ErrorCode::FailedCreateFile;
            }
            if (fs::exists(destination) && !fs::is_directory(destination))
                return ErrorCode::FailedCreateFile;
            ErrorCode ret = PrepareIndexSave(p_folderPath);
            if (ret != ErrorCode::Success) return ret;
            fs::create_directories(destination.parent_path());
            static std::atomic<std::uint64_t> saveSequence{0};
            const auto createStage = [&]()
            {
                for (int attempt = 0; attempt < 32; ++attempt)
                {
                    fs::path candidate = destination;
                    candidate += ".saving." +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
                        "." + std::to_string(saveSequence.fetch_add(1));
                    if (fs::create_directory(candidate)) return candidate;
                }
                return fs::path();
            };
            staging = createStage();
            if (staging.empty()) return ErrorCode::FailedCreateFile;
            // In-place saves need only a read-only validation view, not a
            // second copy of the SSD postings. Never open a linked file for
            // writing: unlink the staged INI before generating its replacement.
            if (!copydirectory(source, staging, sameFolder))
            {
                cleanup();
                return ErrorCode::DiskIOFail;
            }
            ret = spann->PrepareHierarchyExport(staging.string());
            if (ret != ErrorCode::Success)
            {
                cleanup();
                return ret;
            }
            const fs::path stagedConfig = staging / "indexloader.ini";
            fs::remove(stagedConfig);
            SetParameter("IndexDirectory", destination.string(), "Base");
            auto configFile = f_createIO();
            if (configFile == nullptr ||
                !configFile->Initialize(stagedConfig.string().c_str(), std::ios::out))
            {
                cleanup();
                return ErrorCode::FailedCreateFile;
            }
            ret = SaveIndexConfig(configFile);
            const bool configClosed = configFile->ShutDownAndCheck();
            SetParameter("IndexDirectory", configuredFolder, "Base");
            if (ret != ErrorCode::Success || !configClosed)
            {
                cleanup();
                return ret != ErrorCode::Success ? ret : ErrorCode::DiskIOFail;
            }

            // Use the native loader's catalog/CSR/generation validation, rather
            // than treating a successful directory copy as a complete index.
            const auto requireFiles = [](const fs::path& directory,
                                         const std::vector<std::string>& files)
            {
                for (const auto& name : files)
                {
                    const fs::path file = directory / name;
                    if (name.empty() || !fs::is_regular_file(file) || fs::file_size(file) == 0)
                    {
                        SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                                     "Missing or empty index export artifact: %s\n",
                                     file.string().c_str());
                        return false;
                    }
                }
                return true;
            };
            auto requiredFiles = GetIndexFiles();
            requiredFiles->push_back(spann->GetOptions()->m_headIndexFolder +
                                     FolderSep + "head_metaonly.bin");
            requiredFiles->push_back(spann->GetOptions()->m_deleteIDFile);
            requiredFiles->push_back(spann->GetOptions()->m_ssdIndex);
            if (m_pMetadata != nullptr)
            {
                requiredFiles->push_back(m_sMetadataIndexFile);
                if (!fs::is_regular_file(staging / m_sMetadataFile))
                {
                    cleanup();
                    return ErrorCode::FailedOpenFile;
                }
            }
            if (m_pQuantizer != nullptr) requiredFiles->push_back(m_sQuantizerFile);
            if (!requireFiles(staging, *requiredFiles))
            {
                cleanup();
                return ErrorCode::FailedOpenFile;
            }
            std::shared_ptr<VectorIndex> verified;
            ret = LoadIndex(staging.string(), verified);
            if (ret == ErrorCode::Success &&
                (verified->GetNumSamples() != GetNumSamples() ||
                 verified->GetNumDeleted() != GetNumDeleted()))
                ret = ErrorCode::Fail;
            verified.reset();
            if (ret != ErrorCode::Success ||
                (ret = CompleteIndexSave(staging.string())) != ErrorCode::Success)
            {
                cleanup();
                return ret;
            }
            if (sameFolder)
            {
                const bool published = Helper::AtomicReplaceFile(
                    stagedConfig.string(), (destination / "indexloader.ini").string());
                cleanup();
                return published ? ErrorCode::Success : ErrorCode::DiskIOFail;
            }
            if (!Helper::SyncDirectoryTree(staging.string()))
            {
                cleanup();
                return ErrorCode::DiskIOFail;
            }
            // Keep any previous export intact until its replacement has passed
            // validation. A failed copy/load never exposes a partial native INI.
            fs::path previous;
            if (fs::exists(destination))
            {
                previous = createStage();
                if (previous.empty())
                {
                    cleanup();
                    return ErrorCode::FailedCreateFile;
                }
                fs::remove(previous);
                std::error_code error;
                fs::rename(destination, previous, error);
                if (error)
                {
                    cleanup();
                    return ErrorCode::DiskIOFail;
                }
            }
            std::error_code publishError;
            fs::rename(staging, destination, publishError);
            if (publishError)
            {
                if (!previous.empty())
                {
                    std::error_code restoreError;
                    fs::rename(previous, destination, restoreError);
                    if (restoreError)
                        SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                                     "Previous index export retained at %s: %s\n",
                                     previous.string().c_str(), restoreError.message().c_str());
                }
                cleanup();
                return ErrorCode::DiskIOFail;
            }
            staging.clear();
            const bool synced = Helper::SyncParentDirectory(destination.string());
            if (!previous.empty())
            {
                std::error_code error;
                fs::remove_all(previous, error);
                if (error)
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Warning,
                                 "Previous index export retained at %s: %s\n",
                                 previous.string().c_str(), error.message().c_str());
            }
            return synced ? ErrorCode::Success : ErrorCode::DiskIOFail;
        }
        catch (const fs::filesystem_error& error)
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                         "Graphless index export failed: %s\n", error.what());
            cleanup();
            return ErrorCode::DiskIOFail;
        }
        catch (const std::bad_alloc&)
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                         "Insufficient memory to validate graphless index export.\n");
            cleanup();
            return ErrorCode::MemoryOverFlow;
        }
    }
    if (GetIndexAlgoType() == IndexAlgoType::SPANN &&
        !sameFolder)
    {
        ErrorCode ret =
            PrepareIndexSave(p_folderPath);
        if (ret != ErrorCode::Success)
        {
            return ret;
        }
        if (!direxists(p_folderPath.c_str()) &&
            !fs::create_directories(p_folderPath))
        {
            return ErrorCode::FailedCreateFile;
        }
        std::string oldFolder = configuredFolder;
        ret = SaveIndex(oldFolder);
        if (ret != ErrorCode::Success)
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                         "Failed to checkpoint source index directory %s before export!\n",
                         oldFolder.c_str());
            return ret;
        }
        if (!copydirectory(oldFolder, p_folderPath))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to copy index directory contents to %s!\n",
                         p_folderPath.c_str());
            return ErrorCode::Fail;
        }

        SetParameter("IndexDirectory", p_folderPath, "Base");
        auto configFile = SPTAG::f_createIO();
        if (configFile == nullptr || !configFile->Initialize((p_folderPath + FolderSep + "indexloader.ini").c_str(), std::ios::out))
            return ErrorCode::FailedCreateFile;
        ret = SaveIndexConfig(configFile);
        const bool configClosed =
            configFile->ShutDownAndCheck();
        if (ret != ErrorCode::Success)
            return ret;
        if (!configClosed)
            return ErrorCode::DiskIOFail;
        if ((ret = CompleteIndexSave(p_folderPath)) !=
            ErrorCode::Success)
            return ret;

        return ErrorCode::Success;
    }

    std::string folderPath(p_folderPath);
    if (!folderPath.empty() && *(folderPath.rbegin()) != FolderSep)
    {
        folderPath += FolderSep;
    }
    if (!direxists(folderPath.c_str()))
    {
        mkdir(folderPath.c_str());
    }

    ErrorCode ret = ErrorCode::Success;
    auto configFile = SPTAG::f_createIO();
    if (configFile == nullptr)
    {
        return ErrorCode::FailedCreateFile;
    }
    if ((ret = AcquireIndexSaveLock(p_folderPath)) !=
        ErrorCode::Success)
    {
        return ret;
    }
    bool saveLockHeld = true;
    const auto releaseSaveLock = [&]()
    {
        if (!saveLockHeld) return;
        ReleaseIndexSaveLock(p_folderPath);
        saveLockHeld = false;
    };
    if ((ret = PrepareIndexSave(p_folderPath)) !=
        ErrorCode::Success)
    {
        releaseSaveLock();
        return ret;
    }
    {
        if (!configFile->Initialize((folderPath + "indexloader.ini").c_str(), std::ios::out))
        {
            releaseSaveLock();
            return ErrorCode::FailedCreateFile;
        }
        ret = SaveIndexConfig(configFile);
        const bool configClosed =
            configFile->ShutDownAndCheck();
        if (ret != ErrorCode::Success)
        {
            releaseSaveLock();
            return ret;
        }
        if (!configClosed)
        {
            releaseSaveLock();
            return ErrorCode::DiskIOFail;
        }
    }

    std::shared_ptr<std::vector<std::string>> indexfiles = GetIndexFiles();
    if (nullptr != m_pMetadata)
    {
        indexfiles->push_back(m_sMetadataFile);
        indexfiles->push_back(m_sMetadataIndexFile);
    }
    if (m_pQuantizer)
    {
        indexfiles->push_back(m_sQuantizerFile);
    }
    std::vector<std::shared_ptr<Helper::DiskIO>> handles;
    for (std::string &f : *indexfiles)
    {
        std::string newfile = folderPath + f;
        if (!direxists(newfile.substr(0, newfile.find_last_of(FolderSep)).c_str()))
            mkdir(newfile.substr(0, newfile.find_last_of(FolderSep)).c_str());

        auto ptr = SPTAG::f_createIO();
        if (ptr == nullptr || !ptr->Initialize(newfile.c_str(), std::ios::binary | std::ios::out))
        {
            releaseSaveLock();
            return ErrorCode::FailedCreateFile;
        }
        handles.push_back(std::move(ptr));
    }

    if (m_pQuantizer)
    {
        ret = m_pQuantizer->SaveQuantizer(handles.back());
    }

    size_t metaStart = GetIndexFiles()->size();
    if (NeedRefine())
    {
        ret = RefineIndex(handles, nullptr, nullptr);
    }
    else
    {
        if (m_pMetadata != nullptr)
            ret = m_pMetadata->SaveMetadata(handles[metaStart], handles[metaStart + 1]);
        if (ErrorCode::Success == ret)
            ret = SaveIndexData(handles);
    }
    bool handlesClosed = true;
    for (auto& handle : handles)
    {
        if (handle != nullptr &&
            !handle->ShutDownAndCheck())
            handlesClosed = false;
    }
    handles.clear();
    if (ret == ErrorCode::Success &&
        !handlesClosed)
    {
        ret = ErrorCode::DiskIOFail;
    }
    if (ret == ErrorCode::Success)
    {
        ret = CompleteIndexSave(p_folderPath);
    }
    releaseSaveLock();
    return ret;
}

ErrorCode VectorIndex::SaveIndexToFile(const std::string &p_file, IAbortOperation *p_abort)
{
    if (!m_bReady || GetNumSamples() - GetNumDeleted() == 0)
        return ErrorCode::EmptyIndex;
    if (!SupportsNonDirectorySave())
    {
        SPTAGLIB_LOG(
            Helper::LogLevel::LL_Error,
            "Mutable limited-tag H/O indexes do not support SaveIndexToFile.\n");
        return ErrorCode::Undefined;
    }

    auto fp = SPTAG::f_createIO();
    if (fp == nullptr || !fp->Initialize(p_file.c_str(), std::ios::binary | std::ios::out))
        return ErrorCode::FailedCreateFile;

    auto mp = std::shared_ptr<Helper::DiskIO>(new Helper::SimpleBufferIO());
    auto bufsize = 2 << 20;
    std::vector<char> buf(bufsize); // Allocate 1 MB scratch space
    if (mp == nullptr || !mp->Initialize(buf.data(), std::ios::binary | std::ios::out, bufsize))
        return ErrorCode::FailedCreateFile;
    ErrorCode ret = ErrorCode::Success;
    if ((ret = SaveIndexConfig(mp)) != ErrorCode::Success)
        return ret;

    std::uint64_t configSize = mp->TellP();
    mp->ShutDown();

    IOBINARY(fp, WriteBinary, sizeof(configSize), (char *)&configSize);
    if ((ret = SaveIndexConfig(fp)) != ErrorCode::Success)
        return ret;

    if (p_abort != nullptr && p_abort->ShouldAbort())
        ret = ErrorCode::ExternalAbort;
    else
    {
        std::uint64_t blobs = CalculateBufferSize()->size();
        IOBINARY(fp, WriteBinary, sizeof(blobs), (char *)&blobs);
        std::vector<std::shared_ptr<Helper::DiskIO>> p_indexStreams(blobs, fp);

        if (NeedRefine())
        {
            ret = RefineIndex(p_indexStreams, p_abort, nullptr);
        }
        else
        {
            ret = SaveIndexData(p_indexStreams);

            if (p_abort != nullptr && p_abort->ShouldAbort())
                ret = ErrorCode::ExternalAbort;

            if (ErrorCode::Success == ret && m_pMetadata != nullptr)
                ret = m_pMetadata->SaveMetadata(fp, fp);
        }
        if (ErrorCode::Success == ret && m_pQuantizer)
        {
            ret = m_pQuantizer->SaveQuantizer(fp);
        }
    }
    const bool outputClosed =
        fp->ShutDownAndCheck();
    if (ret == ErrorCode::Success &&
        !outputClosed)
        ret = ErrorCode::DiskIOFail;

    if (ret != ErrorCode::Success)
        std::remove(p_file.c_str());
    return ret;
}

ErrorCode VectorIndex::BuildIndex(std::shared_ptr<VectorSet> p_vectorSet, std::shared_ptr<MetadataSet> p_metadataSet,
                                  bool p_withMetaIndex, bool p_normalized, bool p_shareOwnership)
{
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Begin build index...\n");

    bool valueMatches = p_vectorSet->GetValueType() == GetVectorValueType();
    bool quantizerMatches = ((bool)m_pQuantizer) && (p_vectorSet->GetValueType() == SPTAG::VectorValueType::UInt8);
    if (nullptr == p_vectorSet || !(valueMatches || quantizerMatches))
    {
        return ErrorCode::Fail;
    }
    m_pMetadata = std::move(p_metadataSet);
    if (p_withMetaIndex && m_pMetadata != nullptr)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Build meta mapping...\n");
        BuildMetaMapping(false);
    }
    ErrorCode ret = BuildIndex(p_vectorSet->GetData(), p_vectorSet->Count(), p_vectorSet->Dimension(), p_normalized, p_shareOwnership);
    return ret;
}

ErrorCode VectorIndex::SearchIndex(const void *p_vector, int p_vectorCount, int p_neighborCount, bool p_withMeta,
                                   BasicResult *p_results) const
{
    size_t vectorSize = GetValueTypeSize(GetVectorValueType()) * GetFeatureDim();
    std::vector<std::thread> mythreads;
    int maxthreads = std::thread::hardware_concurrency();
    mythreads.reserve(maxthreads);
    std::atomic_size_t sent(0);
    for (int tid = 0; tid < maxthreads; tid++)
    {
        mythreads.emplace_back([&, tid]() {
            size_t i = 0;
            while (true)
            {
                i = sent.fetch_add(1);
                if (i < p_vectorCount)
                {
                    QueryResult res((char *)p_vector + i * vectorSize, p_neighborCount, p_withMeta,
                                    p_results + i * p_neighborCount);
                    SearchIndex(res);
                }
                else
                {
                    return;
                }
            }
        });
    }
    for (auto &t : mythreads)
    {
        t.join();
    }
    mythreads.clear();
    return ErrorCode::Success;
}

ErrorCode VectorIndex::AddIndex(std::shared_ptr<VectorSet> p_vectorSet, std::shared_ptr<MetadataSet> p_metadataSet,
                                bool p_withMetaIndex, bool p_normalized)
{
    if (nullptr == p_vectorSet || p_vectorSet->GetValueType() != GetVectorValueType())
    {
        return ErrorCode::Fail;
    }

    return AddIndex(p_vectorSet->GetData(), p_vectorSet->Count(), p_vectorSet->Dimension(), p_metadataSet,
                    p_withMetaIndex, p_normalized);
}

ErrorCode VectorIndex::DeleteIndex(ByteArray p_meta)
{
    if (m_pMetaToVec == nullptr)
        return ErrorCode::VectorNotFound;

    std::string meta((char *)p_meta.Data(), p_meta.Length());
    SizeType vid = GetMetaMapping(meta);
    if (vid >= 0)
        return DeleteIndex(vid);
    return ErrorCode::VectorNotFound;
}

ErrorCode VectorIndex::MergeIndex(VectorIndex *p_addindex, int p_threadnum, IAbortOperation *p_abort)
{
    ErrorCode ret = ErrorCode::Success;
    if (p_addindex->m_pMetadata != nullptr)
    {
        std::vector<std::thread> mythreads;
        mythreads.reserve(p_threadnum);
        std::atomic_size_t sent(0);
        for (int tid = 0; tid < p_threadnum; tid++)
        {
            mythreads.emplace_back([&, tid]() {
                size_t i = 0;
                while (true)
                {
                    i = sent.fetch_add(1);
                    if (i < p_addindex->GetNumSamples())
                    {
                        if (ret == ErrorCode::ExternalAbort)
                            continue;

                        if (p_addindex->ContainSample(i))
                        {
                            ByteArray meta = p_addindex->GetMetadata(i);
                            std::uint64_t offsets[2] = {0, meta.Length()};
                            std::shared_ptr<MetadataSet> p_metaSet(new MemMetadataSet(
                                meta, ByteArray((std::uint8_t *)offsets, sizeof(offsets), false), 1));
                            AddIndex(p_addindex->GetSample(i), 1, p_addindex->GetFeatureDim(), p_metaSet);
                        }

                        if (p_abort != nullptr && p_abort->ShouldAbort())
                        {
                            ret = ErrorCode::ExternalAbort;
                        }
                    }
                    else
                    {
                        return;
                    }
                }
            });
        }
        for (auto &t : mythreads)
        {
            t.join();
        }
        mythreads.clear();
    }
    else
    {
        std::vector<std::thread> mythreads;
        mythreads.reserve(p_threadnum);
        std::atomic_size_t sent(0);
        for (int tid = 0; tid < p_threadnum; tid++)
        {
            mythreads.emplace_back([&, tid]() {
                size_t i = 0;
                while (true)
                {
                    i = sent.fetch_add(1);
                    if (i < p_addindex->GetNumSamples())
                    {
                        if (ret == ErrorCode::ExternalAbort)
                            continue;

                        if (p_addindex->ContainSample(i))
                        {
                            AddIndex(p_addindex->GetSample(i), 1, p_addindex->GetFeatureDim(), nullptr);
                        }

                        if (p_abort != nullptr && p_abort->ShouldAbort())
                        {
                            ret = ErrorCode::ExternalAbort;
                        }
                    }
                    else
                    {
                        return;
                    }
                }
            });
        }
        for (auto &t : mythreads)
        {
            t.join();
        }
        mythreads.clear();
    }
    return ret;
}

const void *VectorIndex::GetSample(ByteArray p_meta, bool &deleteFlag)
{
    if (m_pMetaToVec == nullptr)
        return nullptr;

    std::string meta((char *)p_meta.Data(), p_meta.Length());
    SizeType vid = GetMetaMapping(meta);
    if (vid >= 0 && vid < GetNumSamples())
    {
        deleteFlag = !ContainSample(vid);
        return GetSample(vid);
    }
    return nullptr;
}

ErrorCode VectorIndex::LoadQuantizer(std::string p_quantizerFile)
{
    auto ptr = SPTAG::f_createIO();
    if (!ptr->Initialize(p_quantizerFile.c_str(), std::ios::binary | std::ios::in))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read quantizer file.\n");
        return ErrorCode::FailedOpenFile;
    }
    SetQuantizer(SPTAG::COMMON::IQuantizer::LoadIQuantizer(ptr));
    if (!m_pQuantizer)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to load quantizer.\n");
        return ErrorCode::FailedParseValue;
    }
    return ErrorCode::Success;
}

std::shared_ptr<VectorIndex> VectorIndex::CreateInstance(IndexAlgoType p_algo, VectorValueType p_valuetype)
{
    if (IndexAlgoType::Undefined == p_algo || VectorValueType::Undefined == p_valuetype)
    {
        return nullptr;
    }

    if (p_algo == IndexAlgoType::BKT)
    {
        switch (p_valuetype)
        {
#define DefineVectorValueType(Name, Type)                                                                              \
    case VectorValueType::Name:                                                                                        \
        return std::shared_ptr<VectorIndex>(new BKT::Index<Type>);

#include "inc/Core/DefinitionList.h"
#undef DefineVectorValueType

        default:
            break;
        }
    }
    else if (p_algo == IndexAlgoType::KDT)
    {
        switch (p_valuetype)
        {
#define DefineVectorValueType(Name, Type)                                                                              \
    case VectorValueType::Name:                                                                                        \
        return std::shared_ptr<VectorIndex>(new KDT::Index<Type>);

#include "inc/Core/DefinitionList.h"
#undef DefineVectorValueType

        default:
            break;
        }
    }
    else if (p_algo == IndexAlgoType::SPANN)
    {
        switch (p_valuetype)
        {
#define DefineVectorValueType(Name, Type)                                                                              \
    case VectorValueType::Name:                                                                                        \
        return std::shared_ptr<VectorIndex>(new SPANN::Index<Type>);

#include "inc/Core/DefinitionList.h"
#undef DefineVectorValueType

        default:
            break;
        }
    }
    return nullptr;
}

ErrorCode VectorIndex::LoadIndex(const std::string &p_loaderFilePath, std::shared_ptr<VectorIndex> &p_vectorIndex)
{
    std::string folderPath(p_loaderFilePath);
    if (!folderPath.empty() && *(folderPath.rbegin()) != FolderSep)
        folderPath += FolderSep;

    Helper::IniReader iniReader;
    {
        auto fp = SPTAG::f_createIO();
        if (fp == nullptr || !fp->Initialize((folderPath + "indexloader.ini").c_str(), std::ios::in))
            return ErrorCode::FailedOpenFile;
        if (ErrorCode::Success != iniReader.LoadIni(fp))
            return ErrorCode::FailedParseValue;
    }

    IndexAlgoType algoType = iniReader.GetParameter("Index", "IndexAlgoType", IndexAlgoType::Undefined);
    VectorValueType valueType = iniReader.GetParameter("Index", "ValueType", VectorValueType::Undefined);
    if ((p_vectorIndex = CreateInstance(algoType, valueType)) == nullptr)
        return ErrorCode::FailedParseValue;

    // Physical root selection must inspect the directory being loaded, not a
    // previous build/export path persisted in its configuration.
    if (algoType == IndexAlgoType::SPANN)
        iniReader.SetParameter("Base", "IndexDirectory", p_loaderFilePath);

    ErrorCode ret = ErrorCode::Success;
    if ((ret = p_vectorIndex->LoadIndexConfig(iniReader)) != ErrorCode::Success)
        return ret;

    std::shared_ptr<std::vector<std::string>> indexfiles = p_vectorIndex->GetIndexFiles();
    if (iniReader.DoesSectionExist("MetaData"))
    {
        indexfiles->push_back(p_vectorIndex->m_sMetadataFile);
        indexfiles->push_back(p_vectorIndex->m_sMetadataIndexFile);
    }
    if (iniReader.DoesSectionExist("Quantizer"))
    {
        indexfiles->push_back(p_vectorIndex->m_sQuantizerFile);
    }
    std::vector<std::shared_ptr<Helper::DiskIO>> handles;
    for (std::string &f : *indexfiles)
    {
        auto ptr = SPTAG::f_createIO();
        if (ptr == nullptr || !ptr->Initialize((folderPath + f).c_str(), std::ios::binary | std::ios::in))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Cannot open file %s!\n", (folderPath + f).c_str());
            ptr = nullptr;
        }
        handles.push_back(std::move(ptr));
    }

    if (algoType == IndexAlgoType::SPANN)
    {
        p_vectorIndex->SetParameter("IndexDirectory", p_loaderFilePath, "Base");
    }

    if ((ret = p_vectorIndex->LoadIndexData(handles)) != ErrorCode::Success)
        return ret;

    size_t metaStart = p_vectorIndex->GetIndexFiles()->size();
    if (iniReader.DoesSectionExist("MetaData"))
    {
        p_vectorIndex->SetMetadata(new MemMetadataSet(handles[metaStart], handles[metaStart + 1],
                                                      p_vectorIndex->m_iDataBlockSize, p_vectorIndex->m_iDataCapacity,
                                                      p_vectorIndex->m_iMetaRecordSize));

        if (!(p_vectorIndex->GetMetadata()->Available()))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Error: Failed to load metadata.\n");
            return ErrorCode::Fail;
        }

        if (iniReader.GetParameter("MetaData", "MetaDataToVectorIndex", std::string()) == "true")
        {
            p_vectorIndex->BuildMetaMapping();
        }
        metaStart += 2;
    }
    if (iniReader.DoesSectionExist("Quantizer"))
    {
        p_vectorIndex->SetQuantizer(SPTAG::COMMON::IQuantizer::LoadIQuantizer(handles[metaStart]));
        if (!p_vectorIndex->m_pQuantizer)
            return ErrorCode::FailedParseValue;
    }
    p_vectorIndex->m_bReady = true;
    return ErrorCode::Success;
}

ErrorCode VectorIndex::LoadIndexFromFile(const std::string &p_file, std::shared_ptr<VectorIndex> &p_vectorIndex)
{
    auto fp = SPTAG::f_createIO();
    if (fp == nullptr || !fp->Initialize(p_file.c_str(), std::ios::binary | std::ios::in))
        return ErrorCode::FailedOpenFile;

    SPTAG::Helper::IniReader iniReader;
    {
        std::uint64_t configSize;
        IOBINARY(fp, ReadBinary, sizeof(configSize), (char *)&configSize);
        std::vector<char> config(configSize + 1, '\0');
        IOBINARY(fp, ReadBinary, configSize, config.data());

        std::shared_ptr<Helper::DiskIO> bufferhandle(new Helper::SimpleBufferIO());
        if (bufferhandle == nullptr || !bufferhandle->Initialize(config.data(), std::ios::in, configSize))
            return ErrorCode::EmptyDiskIO;
        if (SPTAG::ErrorCode::Success != iniReader.LoadIni(bufferhandle))
            return ErrorCode::FailedParseValue;
    }

    IndexAlgoType algoType = iniReader.GetParameter("Index", "IndexAlgoType", IndexAlgoType::Undefined);
    VectorValueType valueType = iniReader.GetParameter("Index", "ValueType", VectorValueType::Undefined);

    ErrorCode ret = ErrorCode::Success;

    if ((p_vectorIndex = CreateInstance(algoType, valueType)) == nullptr)
        return ErrorCode::FailedParseValue;

    if ((ret = p_vectorIndex->LoadIndexConfig(iniReader)) != ErrorCode::Success)
        return ret;

    std::uint64_t blobs;
    IOBINARY(fp, ReadBinary, sizeof(blobs), (char *)&blobs);

    std::vector<std::shared_ptr<Helper::DiskIO>> p_indexStreams(blobs, fp);
    if ((ret = p_vectorIndex->LoadIndexData(p_indexStreams)) != ErrorCode::Success)
        return ret;

    if (iniReader.DoesSectionExist("MetaData"))
    {
        p_vectorIndex->SetMetadata(new MemMetadataSet(
            fp, fp, p_vectorIndex->m_iDataBlockSize, p_vectorIndex->m_iDataCapacity, p_vectorIndex->m_iMetaRecordSize));

        if (!(p_vectorIndex->GetMetadata()->Available()))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Error: Failed to load metadata.\n");
            return ErrorCode::Fail;
        }

        if (iniReader.GetParameter("MetaData", "MetaDataToVectorIndex", std::string()) == "true")
        {
            p_vectorIndex->BuildMetaMapping();
        }
    }

    if (iniReader.DoesSectionExist("Quantizer"))
    {
        p_vectorIndex->SetQuantizer(SPTAG::COMMON::IQuantizer::LoadIQuantizer(fp));
        if (!p_vectorIndex->m_pQuantizer)
            return ErrorCode::FailedParseValue;
    }

    p_vectorIndex->m_bReady = true;
    return ErrorCode::Success;
}

ErrorCode VectorIndex::LoadIndex(const std::string &p_config, const std::vector<ByteArray> &p_indexBlobs,
                                 std::shared_ptr<VectorIndex> &p_vectorIndex)
{
    SPTAG::Helper::IniReader iniReader;
    std::shared_ptr<Helper::DiskIO> fp(new Helper::SimpleBufferIO());
    if (fp == nullptr || !fp->Initialize(p_config.c_str(), std::ios::in, p_config.size()))
        return ErrorCode::EmptyDiskIO;
    if (SPTAG::ErrorCode::Success != iniReader.LoadIni(fp))
        return ErrorCode::FailedParseValue;

    IndexAlgoType algoType = iniReader.GetParameter("Index", "IndexAlgoType", IndexAlgoType::Undefined);
    VectorValueType valueType = iniReader.GetParameter("Index", "ValueType", VectorValueType::Undefined);

    ErrorCode ret = ErrorCode::Success;

    if ((p_vectorIndex = CreateInstance(algoType, valueType)) == nullptr)
        return ErrorCode::FailedParseValue;
    if (!iniReader.GetParameter<std::string>("Base", "QuantizerFilePath", std::string()).empty())
    {
        p_vectorIndex->SetQuantizer(COMMON::IQuantizer::LoadIQuantizer(p_indexBlobs[4]));
        if (!p_vectorIndex->m_pQuantizer)
            return ErrorCode::FailedParseValue;
    }

    if ((p_vectorIndex->LoadIndexConfig(iniReader)) != ErrorCode::Success)
        return ret;

    if ((ret = p_vectorIndex->LoadIndexDataFromMemory(p_indexBlobs)) != ErrorCode::Success)
        return ret;

    size_t metaStart = p_vectorIndex->BufferSize()->size();
    if (iniReader.DoesSectionExist("MetaData") && p_indexBlobs.size() >= metaStart + 2)
    {
        ByteArray pMetaIndex = p_indexBlobs[metaStart + 1];
        p_vectorIndex->SetMetadata(new MemMetadataSet(
            p_indexBlobs[metaStart],
            ByteArray(pMetaIndex.Data() + sizeof(SizeType), pMetaIndex.Length() - sizeof(SizeType), false),
            *((SizeType *)pMetaIndex.Data()), p_vectorIndex->m_iDataBlockSize, p_vectorIndex->m_iDataCapacity,
            p_vectorIndex->m_iMetaRecordSize));

        if (!(p_vectorIndex->GetMetadata()->Available()))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Error: Failed to load metadata.\n");
            return ErrorCode::Fail;
        }

        if (iniReader.GetParameter("MetaData", "MetaDataToVectorIndex", std::string()) == "true")
        {
            p_vectorIndex->BuildMetaMapping();
        }
        metaStart += 2;
    }

    p_vectorIndex->m_bReady = true;
    return ErrorCode::Success;
}

std::shared_ptr<VectorIndex> VectorIndex::Clone(std::string p_clone)
{
    ErrorCode ret = ErrorCode::Success;
    if (GetIndexAlgoType() != IndexAlgoType::SPANN)
        ret = SaveIndex(p_clone);
    else
    {
        std::string indexFolder = GetParameter("IndexDirectory", "Base");
        ret = SaveIndex(indexFolder);
        if (!copydirectory(indexFolder, p_clone))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to copy index directory contents to %s!\n",
                         p_clone.c_str());
            return nullptr;
        }
    }
    if (ret != ErrorCode::Success)
        return nullptr;

    std::shared_ptr<VectorIndex> clone;
    auto status = VectorIndex::LoadIndex(p_clone, clone);
    if (status != ErrorCode::Success)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to load index from %s!\n", p_clone.c_str());
        return nullptr;
    }
    return clone;
}

std::uint64_t VectorIndex::EstimatedVectorCount(std::uint64_t p_memory, DimensionType p_dimension,
                                                VectorValueType p_valuetype, SizeType p_vectorsInBlock,
                                                SizeType p_maxmeta, IndexAlgoType p_algo, int p_treeNumber,
                                                int p_neighborhoodSize)
{
    size_t treeNodeSize;
    if (p_algo == IndexAlgoType::BKT)
    {
        treeNodeSize = sizeof(SizeType) * 3;
    }
    else if (p_algo == IndexAlgoType::KDT)
    {
        treeNodeSize = sizeof(SizeType) * 2 + sizeof(DimensionType) + sizeof(float);
    }
    else
    {
        return 0;
    }
    std::uint64_t unit = GetValueTypeSize(p_valuetype) * p_dimension + p_maxmeta + sizeof(std::uint64_t) +
                         sizeof(SizeType) * p_neighborhoodSize + 1 + treeNodeSize * p_treeNumber;
    return ((p_memory / unit) / p_vectorsInBlock) * p_vectorsInBlock;
}

std::uint64_t VectorIndex::EstimatedMemoryUsage(std::uint64_t p_vectorCount, DimensionType p_dimension,
                                                VectorValueType p_valuetype, SizeType p_vectorsInBlock,
                                                SizeType p_maxmeta, IndexAlgoType p_algo, int p_treeNumber,
                                                int p_neighborhoodSize)
{
    p_vectorCount = ((p_vectorCount + p_vectorsInBlock - 1) / p_vectorsInBlock) * p_vectorsInBlock;
    size_t treeNodeSize;
    if (p_algo == IndexAlgoType::BKT)
    {
        treeNodeSize = sizeof(SizeType) * 3;
    }
    else if (p_algo == IndexAlgoType::KDT)
    {
        treeNodeSize = sizeof(SizeType) * 2 + sizeof(DimensionType) + sizeof(float);
    }
    else
    {
        return 0;
    }
    std::uint64_t ret = GetValueTypeSize(p_valuetype) * p_dimension * p_vectorCount; // Vector Size
    ret += p_maxmeta * p_vectorCount;                                                // MetaData Size
    ret += sizeof(std::uint64_t) * p_vectorCount;                                    // MetaIndex Size
    ret += sizeof(SizeType) * p_neighborhoodSize * p_vectorCount;                    // Graph Size
    ret += p_vectorCount;                                                            // DeletedFlag Size
    ret += treeNodeSize * p_treeNumber * p_vectorCount;                              // Tree Size
    return ret;
}

#if defined(GPU)

#include "inc/Core/Common/cuda/TailNeighbors.hxx"

void VectorIndex::SortSelections(std::vector<Edge> *selections)
{
    SPTAGLIB_LOG(Helper::LogLevel::LL_Debug, "Starting sort of final input on GPU\n");
    GPU_SortSelections(selections);
}

void VectorIndex::ApproximateRNG(std::shared_ptr<VectorSet> &fullVectors, std::unordered_set<SizeType> &exceptIDS,
                                 int candidateNum, Edge *selections, int replicaCount, int numThreads, int numTrees,
                                 int leafSize, float RNGFactor, int numGPUs)
{

    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Starting GPU SSD Index build stage...\n");

    int metric = (GetDistCalcMethod() == SPTAG::DistCalcMethod::Cosine);

    if (m_pQuantizer)
    {
        getTailNeighborsTPT<uint8_t, float>((uint8_t *)fullVectors->GetData(), fullVectors->Count(), this, exceptIDS,
                                            fullVectors->Dimension(), replicaCount, numThreads, numTrees, leafSize,
                                            metric, numGPUs, selections);
    }
    else if (GetVectorValueType() != VectorValueType::Float)
    {
        typedef int32_t SUMTYPE;
        switch (GetVectorValueType())
        {
#define DefineVectorValueType(Name, Type)                                                                              \
    case VectorValueType::Name:                                                                                        \
        getTailNeighborsTPT<Type, SUMTYPE>((Type *)fullVectors->GetData(), fullVectors->Count(), this, exceptIDS,      \
                                           fullVectors->Dimension(), replicaCount, numThreads, numTrees, leafSize,     \
                                           metric, numGPUs, selections);                                               \
        break;

#include "inc/Core/DefinitionList.h"
#undef DefineVectorValueType

        default:
            break;
        }
    }
    else
    {
        getTailNeighborsTPT<float, float>((float *)fullVectors->GetData(), fullVectors->Count(), this, exceptIDS,
                                          fullVectors->Dimension(), replicaCount, numThreads, numTrees, leafSize,
                                          metric, numGPUs, selections);
    }
}
#else

void VectorIndex::SortSelections(std::vector<Edge> *selections)
{
    EdgeCompare edgeComparer;
    std::sort(selections->begin(), selections->end(), edgeComparer);
}

void VectorIndex::ApproximateRNG(std::shared_ptr<VectorSet> &fullVectors, std::unordered_set<SizeType> &exceptIDS,
                                 int candidateNum, Edge *selections, int replicaCount, int numThreads, int numTrees,
                                 int leafSize, float RNGFactor, int numGPUs)
{
    std::vector<std::thread> threads;
    threads.reserve(numThreads);

    std::atomic_int nextFullID(0);
    std::atomic_size_t rngFailedCountTotal(0);

    for (int tid = 0; tid < numThreads; ++tid)
    {
        threads.emplace_back([&, tid]() {
            QueryResult resultSet(NULL, candidateNum, false);

            size_t rngFailedCount = 0;

            while (true)
            {
                int fullID = nextFullID.fetch_add(1);
                if (fullID >= fullVectors->Count())
                {
                    break;
                }

                if (exceptIDS.count(fullID) > 0)
                {
                    continue;
                }

                void *reconstructed_vector = nullptr;
                if (m_pQuantizer)
                {
                    reconstructed_vector = ALIGN_ALLOC(m_pQuantizer->ReconstructSize());
                    m_pQuantizer->ReconstructVector((const uint8_t *)fullVectors->GetVector(fullID),
                                                    reconstructed_vector);
                    switch (m_pQuantizer->GetReconstructType())
                    {
#define DefineVectorValueType(Name, Type)                                                                              \
    case VectorValueType::Name:                                                                                        \
        (*((COMMON::QueryResultSet<Type> *)&resultSet))                                                                \
            .SetTarget(reinterpret_cast<Type *>(reconstructed_vector), m_pQuantizer);                                  \
        break;
#include "inc/Core/DefinitionList.h"
#undef DefineVectorValueType
                    default:
                        SPTAGLIB_LOG(
                            Helper::LogLevel::LL_Error, "Unable to get quantizer reconstruct type %s",
                            Helper::Convert::ConvertToString<VectorValueType>(m_pQuantizer->GetReconstructType()));
                    }
                }
                else
                {
                    resultSet.SetTarget(fullVectors->GetVector(fullID));
                }
                resultSet.Reset();

                SearchIndex(resultSet);

                size_t selectionOffset = static_cast<size_t>(fullID) * replicaCount;

                BasicResult *queryResults = resultSet.GetResults();
                int currReplicaCount = 0;
                for (int i = 0; i < candidateNum && currReplicaCount < replicaCount; ++i)
                {
                    if (queryResults[i].VID == -1)
                    {
                        break;
                    }

                    // RNG Check.
                    bool rngAccpeted = true;
                    for (int j = 0; j < currReplicaCount; ++j)
                    {
                        float nnDist = ComputeDistance(GetSample(queryResults[i].VID),
                                                       GetSample(selections[selectionOffset + j].node));

                        if (RNGFactor * nnDist < queryResults[i].Dist)
                        {
                            rngAccpeted = false;
                            break;
                        }
                    }

                    if (!rngAccpeted)
                    {
                        ++rngFailedCount;
                        continue;
                    }

                    selections[selectionOffset + currReplicaCount].node = queryResults[i].VID;
                    selections[selectionOffset + currReplicaCount].distance = queryResults[i].Dist;
                    ++currReplicaCount;
                }

                if (reconstructed_vector)
                {
                    ALIGN_FREE(reconstructed_vector);
                }
            }
            rngFailedCountTotal += rngFailedCount;
        });
    }

    for (int tid = 0; tid < numThreads; ++tid)
    {
        threads[tid].join();
    }
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Searching replicas ended. RNG failed count: %llu\n",
                 static_cast<uint64_t>(rngFailedCountTotal.load()));
}
#endif

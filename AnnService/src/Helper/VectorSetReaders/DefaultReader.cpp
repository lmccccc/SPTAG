// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Helper/VectorSetReaders/DefaultReader.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Helper/CommonHelper.h"
#include <limits>

using namespace SPTAG;
using namespace SPTAG::Helper;

DefaultVectorReader::DefaultVectorReader(std::shared_ptr<ReaderOptions> p_options) : VectorSetReader(p_options)
{
    m_vectorOutput = "";
    m_metadataConentOutput = "";
    m_metadataIndexOutput = "";
}

DefaultVectorReader::~DefaultVectorReader()
{
}

ErrorCode DefaultVectorReader::LoadFile(const std::string &p_filePaths)
{
    const auto &files = SPTAG::Helper::StrUtils::SplitString(p_filePaths, ",");
    if (files.empty() || files[0].empty() || (files.size() != 1 && files.size() != 3))
        return ErrorCode::FailedOpenFile;
    m_vectorOutput = files[0];
    if (files.size() >= 3)
    {
        m_metadataConentOutput = files[1];
        m_metadataIndexOutput = files[2];
    }
    return ErrorCode::Success;
}

std::shared_ptr<VectorSet> DefaultVectorReader::GetVectorSet(SizeType start, SizeType end) const
{
    auto ptr = f_createIO();
    if (ptr == nullptr || !ptr->Initialize(m_vectorOutput.c_str(), std::ios::binary | std::ios::in))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read file %s.\n", m_vectorOutput.c_str());
        throw std::runtime_error("Failed read file");
    }

    SizeType row;
    DimensionType col;
    if (ptr->ReadBinary(sizeof(SizeType), (char *)&row) != sizeof(SizeType))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read VectorSet!\n");
        throw std::runtime_error("Failed read file");
    }
    if (ptr->ReadBinary(sizeof(DimensionType), (char *)&col) != sizeof(DimensionType))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read VectorSet!\n");
        throw std::runtime_error("Failed read file");
    }

    const std::uint64_t valueBytes = GetValueTypeSize(m_options->m_inputValueType);
    const std::uint64_t headerBytes = sizeof(SizeType) + sizeof(DimensionType);
    if (row < 0 || col <= 0 || valueBytes == 0 ||
        (m_options->m_dimension > 0 && col != m_options->m_dimension))
        throw std::runtime_error("Invalid DEFAULT row/dimension/ValueType (Dim must match header)");
    const std::uint64_t stride = valueBytes * static_cast<std::uint64_t>(col);
    if (stride > static_cast<std::uint64_t>(MaxSize) ||
        static_cast<std::uint64_t>(row) > (std::numeric_limits<std::size_t>::max() - headerBytes) / stride)
        throw std::runtime_error("DEFAULT vector byte size overflow");
    const std::uint64_t expectedSize = headerBytes + static_cast<std::uint64_t>(row) * stride;
    std::uint64_t fileSize = 0;
    if (!ptr->GetFileSize(fileSize) || fileSize != expectedSize)
        throw std::runtime_error("DEFAULT file size disagrees with row/dimension/ValueType; raw input is unsupported");
    m_sourceCount = row;
    if (start < 0 || end < -1 || (end >= 0 && end < start))
        throw std::runtime_error("Invalid vector range");
    if (start > row)
        start = row;
    if (end < 0 || end > row)
        end = row;
    const std::uint64_t totalRecordVectorBytes = stride * (end - start);
    ByteArray vectorSet;
    if (totalRecordVectorBytes > 0)
    {
        const std::uint64_t offset = stride * start + headerBytes;
        auto mapped = m_options->m_readOnlyMapped ? ptr->MapReadOnly(fileSize) : nullptr;
        if (mapped)
        {
            // Check the mapped header too: never interpret a replaced file with stale dimensions.
            if (memcmp(mapped.get(), &row, sizeof(row)) != 0 ||
                memcmp(mapped.get() + sizeof(row), &col, sizeof(col)) != 0)
                throw std::runtime_error("DEFAULT input changed while opening mapping");
            vectorSet = ByteArray(mapped.get() + offset, totalRecordVectorBytes, mapped);
        }
        else
        {
            vectorSet = ByteArray::Alloc(totalRecordVectorBytes);
            if (ptr->ReadBinary(totalRecordVectorBytes, reinterpret_cast<char*>(vectorSet.Data()), offset) != totalRecordVectorBytes)
                throw std::runtime_error("Failed reading DEFAULT vector payload");
        }
    }

    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Load Vector(%d,%d)\n", end - start, col);
    return std::make_shared<BasicVectorSet>(vectorSet, m_options->m_inputValueType, col, end - start);
}

std::shared_ptr<MetadataSet> DefaultVectorReader::GetMetadataSet() const
{
    if (fileexists(m_metadataIndexOutput.c_str()) && fileexists(m_metadataConentOutput.c_str()))
        return std::shared_ptr<MetadataSet>(new FileMetadataSet(m_metadataConentOutput, m_metadataIndexOutput));
    return nullptr;
}

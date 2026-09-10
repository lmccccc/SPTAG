// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Helper/VectorSetReaders/XvecReader.h"
#include "inc/Helper/VectorSetReaders/DefaultReader.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Helper/CommonHelper.h"
#include <time.h>

using namespace SPTAG;
using namespace SPTAG::Helper;

XvecVectorReader::XvecVectorReader(std::shared_ptr<ReaderOptions> p_options) : VectorSetReader(p_options)
{
    std::string tempFolder("tempfolder");
    if (!direxists(tempFolder.c_str()))
    {
        mkdir(tempFolder.c_str());
    }
    std::srand(clock());
    m_vectorOutput = tempFolder + FolderSep + "vectorset.bin." + std::to_string(std::rand());
}

XvecVectorReader::~XvecVectorReader()
{
    if (fileexists(m_vectorOutput.c_str()))
    {
        remove(m_vectorOutput.c_str());
    }
}

ErrorCode XvecVectorReader::LoadFile(const std::string &p_filePaths)
{
    if (m_options->m_dimension <= 0 ||
        GetValueTypeSize(m_options->m_inputValueType) == 0 ||
        static_cast<std::uint64_t>(m_options->m_dimension) *
            GetValueTypeSize(m_options->m_inputValueType) > static_cast<std::uint64_t>(MaxSize))
        return ErrorCode::DimensionSizeMismatch;
    const auto &files = Helper::StrUtils::SplitString(p_filePaths, ",");
    auto fp = f_createIO();
    if (fp == nullptr || !fp->Initialize(m_vectorOutput.c_str(), std::ios::binary | std::ios::out))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to write file: %s \n", m_vectorOutput.c_str());
        return ErrorCode::FailedCreateFile;
    }
    SizeType vectorCount = 0;
    IOBINARY(fp, WriteBinary, sizeof(vectorCount), (char *)&vectorCount);
    IOBINARY(fp, WriteBinary, sizeof(m_options->m_dimension), (char *)&(m_options->m_dimension));

    size_t vectorDataSize = GetValueTypeSize(m_options->m_inputValueType) * m_options->m_dimension;
    std::unique_ptr<char[]> buffer(new char[vectorDataSize]);
    for (std::string file : files)
    {
        auto ptr = f_createIO();
        if (ptr == nullptr || !ptr->Initialize(file.c_str(), std::ios::binary | std::ios::in))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read file: %s \n", file.c_str());
            return ErrorCode::FailedOpenFile;
        }
        while (true)
        {
            DimensionType dim;
            const auto bytes = ptr->ReadBinary(sizeof(DimensionType), (char *)&dim);
            if (bytes == 0)
                break;
            if (bytes != sizeof(DimensionType) || vectorCount == MaxSize)
                return ErrorCode::FailedParseValue;

            if (dim != m_options->m_dimension)
            {
                SPTAGLIB_LOG(
                    Helper::LogLevel::LL_Error,
                    "Xvec file %s has No.%d vector whose dims are not as many as expected. Expected: %d, Fact: %d\n",
                    file.c_str(), vectorCount, m_options->m_dimension, dim);
                return ErrorCode::DimensionSizeMismatch;
            }
            IOBINARY(ptr, ReadBinary, vectorDataSize, buffer.get());
            IOBINARY(fp, WriteBinary, vectorDataSize, buffer.get());
            vectorCount++;
        }
    }
    IOBINARY(fp, WriteBinary, sizeof(vectorCount), (char *)&vectorCount, 0);
    return ErrorCode::Success;
}

std::shared_ptr<VectorSet> XvecVectorReader::GetVectorSet(SizeType start, SizeType end) const
{
    DefaultVectorReader reader(m_options);
    if (reader.LoadFile(m_vectorOutput) != ErrorCode::Success)
        throw std::runtime_error("Failed opening converted XVEC input");
    auto vectors = reader.GetVectorSet(start, end);
    m_sourceCount = reader.SourceCount();
    return vectors;
}

std::shared_ptr<MetadataSet> XvecVectorReader::GetMetadataSet() const
{
    return nullptr;
}

// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_HELPER_NATIVEATTRIBUTEREADER_H_
#define _SPTAG_HELPER_NATIVEATTRIBUTEREADER_H_

#include "inc/Core/CommonDataStructure.h"
#include "inc/Helper/DiskIO.h"
#include <limits>
#include <stdexcept>

namespace SPTAG { namespace Helper {

// Headerless, native uint32 row-major records. No byte offset or format inference.
inline std::size_t NativeAttributeBytes(std::uint64_t rows, std::uint64_t columns)
{
    if (rows == 0 || rows > MaxSize || columns == 0 || columns > MaxSize ||
        columns > static_cast<std::uint64_t>(MaxSize) / sizeof(std::uint32_t) ||
        rows > std::numeric_limits<std::size_t>::max() / sizeof(std::uint32_t) / columns)
        throw std::runtime_error("Invalid native TagFile row count/NumTagsPerVec or byte size overflow");
    return static_cast<std::size_t>(rows * columns * sizeof(std::uint32_t));
}

inline ByteArray ReadNativeAttributes(const char* path, SizeType rows,
                                     SizeType sourceRows, int columns)
{
    const auto bytes = NativeAttributeBytes(rows, columns);
    const auto fullBytes = NativeAttributeBytes(sourceRows, columns);
    if (rows > sourceRows)
        throw std::runtime_error("Native TagFile prefix exceeds vector source rows");
    auto io = f_createIO();
    if (!io || !io->Initialize(path, std::ios::binary | std::ios::in))
        throw std::runtime_error("Cannot open native TagFile");
    std::uint64_t size = 0;
    if (!io->GetFileSize(size) || (size != bytes && size != fullBytes))
        throw std::runtime_error("Native TagFile must be headerless row-major uint32: exact selected or full vector row count times NumTagsPerVec times 4 bytes");
    auto mapped = io->MapReadOnly(size);
    if (mapped) return ByteArray(mapped.get(), bytes, mapped);
    auto result = ByteArray::Alloc(bytes);
    if (io->ReadBinary(bytes, reinterpret_cast<char*>(result.Data()), 0) != bytes)
        throw std::runtime_error("Truncated native TagFile payload");
    return result;
}

}}
#endif

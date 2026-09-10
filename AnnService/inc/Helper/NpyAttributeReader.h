// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_HELPER_NPYATTRIBUTEREADER_H_
#define _SPTAG_HELPER_NPYATTRIBUTEREADER_H_

#include "inc/Helper/NativeAttributeReader.h"
#include <map>
#include <regex>

namespace SPTAG { namespace Helper {

struct NpyAttributes {
    ByteArray data;
    SizeType rows;
};

// Only the explicit --merge-tags5 NPY input uses this format, never TagFile.
inline NpyAttributes ReadNpyAttributes(const char* path, int columns, bool numeric)
{
    auto io = f_createIO();
    if (!io || !io->Initialize(path, std::ios::binary | std::ios::in))
        throw std::runtime_error("Cannot open NPY attribute input");
    unsigned char prefix[10];
    std::uint64_t size = 0;
    if (!io->GetFileSize(size) || io->ReadBinary(10, reinterpret_cast<char*>(prefix), 0) != 10 ||
        std::memcmp(prefix, "\x93NUMPY", 6) != 0 || prefix[6] != 1 || prefix[7] != 0)
        throw std::runtime_error("Attribute preparation requires explicit NPY v1.0 input");
    const std::size_t headerBytes = prefix[8] | (static_cast<std::size_t>(prefix[9]) << 8);
    const std::size_t payload = 10 + headerBytes;
    if (headerBytes == 0 || payload > size || payload % 16 != 0)
        throw std::runtime_error("Truncated or misaligned NPY header");
    std::string header(headerBytes, '\0');
    if (io->ReadBinary(headerBytes, &header[0], 10) != headerBytes || header.back() != '\n')
        throw std::runtime_error("Truncated NPY header");
    // NPY's literal dictionary is not executable code. Accept its specified keys
    // in any order, rejecting unknown/duplicate fields and unsupported layouts.
    const std::regex field(R"(['"](descr|fortran_order|shape)['"]\s*:\s*('[^']*'|"[^"]*"|False|True|\([^)]*\))\s*(,?)\s*)");
    std::smatch match;
    const auto begin = header.find('{'), end = header.rfind('}');
    if (begin == std::string::npos || end == std::string::npos || end <= begin ||
        header.substr(0, begin).find_first_not_of(" \t") != std::string::npos ||
        header.substr(end + 1).find_first_not_of(" \t\n") != std::string::npos)
        throw std::runtime_error("Invalid NPY header dictionary");
    std::string body = header.substr(begin + 1, end - begin - 1);
    std::map<std::string, std::string> fields;
    while (body.find_first_not_of(" \t") != std::string::npos) {
        body.erase(0, body.find_first_not_of(" \t"));
        if (!std::regex_search(body, match, field, std::regex_constants::match_continuous) ||
            !fields.emplace(match[1].str(), match[2].str()).second)
            throw std::runtime_error("Invalid or duplicate NPY header field");
        if (match[3].str().empty() && match.length() != static_cast<long>(body.size()))
            throw std::runtime_error("Missing NPY dictionary field separator");
        body.erase(0, match.length());
    }
    const std::uint16_t endian = 1;
    const auto dtype = fields["descr"];
    if (fields.size() != 3 || fields["fortran_order"] != "False" ||
        *reinterpret_cast<const unsigned char*>(&endian) != 1 ||
        (dtype != (numeric ? "'<i4'" : "'<u4'") && dtype != (numeric ? "\"<i4\"" : "\"<u4\"")))
        throw std::runtime_error("NPY attributes require C-order little-endian uint32 tags / int32 numeric values");
    const std::regex shape(numeric ? R"(\(\s*([0-9]+)\s*,\s*\))" :
                                    R"(\(\s*([0-9]+)\s*,\s*([0-9]+)\s*,?\s*\))");
    if (!std::regex_match(fields["shape"], match, shape))
        throw std::runtime_error("NPY attributes require tags[N,acl-cols] and numeric[N]");
    const auto rows = std::stoull(match[1].str());
    if (!numeric && std::stoull(match[2].str()) != static_cast<std::uint64_t>(columns))
        throw std::runtime_error("NPY tag shape disagrees with acl-cols");
    const auto bytes = NativeAttributeBytes(rows, numeric ? 1 : columns);
    if (size < payload || size - payload != bytes)
        throw std::runtime_error("NPY shape/type disagrees with exact payload size");
    auto mapped = io->MapReadOnly(size);
    ByteArray data;
    if (mapped) data = ByteArray(mapped.get() + payload, bytes, mapped);
    else {
        data = ByteArray::Alloc(bytes);
        if (io->ReadBinary(bytes, reinterpret_cast<char*>(data.Data()), payload) != bytes)
            throw std::runtime_error("Truncated NPY attribute payload");
    }
    return {data, static_cast<SizeType>(rows)};
}

}}
#endif

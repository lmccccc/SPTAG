// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Test.h"
#include "inc/Helper/VectorSetReader.h"
#include "inc/Helper/VectorSetReaders/MemoryReader.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Helper/NativeAttributeReader.h"
#include "inc/Helper/NpyAttributeReader.h"
#include <filesystem>
#include <fstream>

using namespace SPTAG;

namespace {
std::uint64_t countedVectorBytes = 0;
class CountingVectorIO : public Helper::SimpleFileIO {
public:
    bool Initialize(const char*, int, std::uint64_t, std::uint32_t,
                    std::uint32_t, std::uint16_t, std::uint64_t) override { return true; }
    std::uint64_t WriteBinary(std::uint64_t bytes, const char*, std::uint64_t) override {
        countedVectorBytes = bytes;
        return bytes;
    }
};

struct NativeInput {
    std::string path = "native_vector_reader_fixture.bin";
    ~NativeInput() { std::filesystem::remove(path); }
    void Write(int rows, int cols, const std::string& payload) {
        std::ofstream out(path, std::ios::binary | std::ios::trunc);
        out.write(reinterpret_cast<char*>(&rows), sizeof(rows));
        out.write(reinterpret_cast<char*>(&cols), sizeof(cols));
        out.write(payload.data(), payload.size());
    }
    std::shared_ptr<Helper::VectorSetReader> Reader(bool mapped, int dim = 0,
        VectorValueType value = VectorValueType::UInt8,
        VectorFileType container = VectorFileType::DEFAULT) {
        auto options = std::make_shared<Helper::ReaderOptions>(value, dim, container);
        options->m_readOnlyMapped = mapped;
        auto reader = Helper::VectorSetReader::CreateInstance(options);
        BOOST_REQUIRE(reader);
        BOOST_REQUIRE(reader->LoadFile(path) == ErrorCode::Success);
        return reader;
    }
};
}

BOOST_AUTO_TEST_SUITE(NativeVectorReaderTest)

BOOST_AUTO_TEST_CASE(DefaultHeaderPrefixAndOwnedLifetime)
{
    NativeInput input;
    input.Write(3, 2, std::string("\x01\xff\x03\x04\x05\x06", 6));
    for (bool mapped : {false, true}) {
        auto reader = input.Reader(mapped);
        auto all = reader->GetVectorSet();
        BOOST_CHECK_EQUAL(reader->SourceCount(), 3);
        BOOST_CHECK_EQUAL(all->Count(), 3);
        BOOST_CHECK_EQUAL(all->Dimension(), 2);
        BOOST_CHECK(all->GetValueType() == VectorValueType::UInt8);
        auto prefix = reader->GetVectorSet(0, 2);
        BOOST_CHECK_EQUAL(reader->SourceCount(), 3);
        BOOST_CHECK_EQUAL(prefix->Count(), 2);
        auto last = reader->GetVectorSet(2, -1);
        BOOST_CHECK_EQUAL(last->Count(), 1);
        BOOST_CHECK_EQUAL(static_cast<unsigned char*>(last->GetData())[0], 5);
        BOOST_CHECK_EQUAL(reader->GetVectorSet(0, 99)->Count(), 3);
        BOOST_CHECK_THROW(reader->GetVectorSet(-1), std::runtime_error);
        BOOST_CHECK_THROW(reader->GetVectorSet(2, 1), std::runtime_error);
        BOOST_CHECK_THROW(reader->GetVectorSet(0, -2), std::runtime_error);
        reader.reset();
        BOOST_CHECK_EQUAL(static_cast<unsigned char*>(all->GetData())[1], 255);
    }
}

BOOST_AUTO_TEST_CASE(DefaultRejectsMalformedBeforeAllocation)
{
    NativeInput input;
    for (bool mapped : {false, true}) {
        for (const auto& shape : {std::pair<int,int>{-1,2}, {2,-1}, {2,0},
                                  {2147483647,2147483647}, {3,2}}) {
            input.Write(shape.first, shape.second, "");
            BOOST_CHECK_THROW(input.Reader(mapped)->GetVectorSet(), std::runtime_error);
        }
        input.Write(3, 2, "12345");
        BOOST_CHECK_THROW(input.Reader(mapped)->GetVectorSet(0, 1), std::runtime_error);
        input.Write(3, 2, "1234567");
        BOOST_CHECK_THROW(input.Reader(mapped)->GetVectorSet(), std::runtime_error);
        input.Write(3, 2, "123456");
        BOOST_CHECK_THROW(input.Reader(mapped, 3)->GetVectorSet(), std::runtime_error);
        BOOST_CHECK_THROW(input.Reader(mapped, 2, VectorValueType::Float)->GetVectorSet(), std::runtime_error);
        std::ofstream(input.path, std::ios::binary | std::ios::trunc).write("123", 3);
        BOOST_CHECK_THROW(input.Reader(mapped)->GetVectorSet(), std::runtime_error);
    }
}

BOOST_AUTO_TEST_CASE(XvecNativeContainerAndPartialHeader)
{
    NativeInput input;
    {
        std::ofstream out(input.path, std::ios::binary);
        const int dimension = 2;
        for (int i = 0; i < 3; ++i) {
            out.write(reinterpret_cast<const char*>(&dimension), 4);
            out.write("ab", 2);
        }
    }
    auto reader = input.Reader(true, 2, VectorValueType::UInt8, VectorFileType::XVEC);
    auto vectors = reader->GetVectorSet(0, 2);
    BOOST_CHECK_EQUAL(reader->SourceCount(), 3);
    BOOST_CHECK_EQUAL(vectors->Count(), 2);
    BOOST_CHECK_EQUAL(vectors->Dimension(), 2);
    BOOST_CHECK_EQUAL(static_cast<char*>(vectors->GetData())[3], 'b');
    reader.reset();
    BOOST_CHECK_EQUAL(static_cast<char*>(vectors->GetData())[0], 'a');
    auto options = std::make_shared<Helper::ReaderOptions>(VectorValueType::UInt8, 2, VectorFileType::XVEC);
    for (const std::string tail : {"x", "xy", "xyz"}) {
        std::ofstream(input.path, std::ios::binary | std::ios::trunc).write(tail.data(), tail.size());
        BOOST_CHECK(Helper::VectorSetReader::CreateInstance(options)->LoadFile(input.path) != ErrorCode::Success);
    }
    input.Write(3, 2, "ab");
    BOOST_CHECK(Helper::VectorSetReader::CreateInstance(options)->LoadFile(input.path) != ErrorCode::Success);
    {
        const int dimension = 2;
        std::ofstream out(input.path, std::ios::binary | std::ios::trunc);
        out.write(reinterpret_cast<const char*>(&dimension), 4);
        out.write("a", 1);
    }
    BOOST_CHECK(Helper::VectorSetReader::CreateInstance(options)->LoadFile(input.path) != ErrorCode::Success);
}

BOOST_AUTO_TEST_CASE(TxtNativeConversionAndDimensionFailure)
{
    NativeInput input;
    auto options = std::make_shared<Helper::ReaderOptions>(VectorValueType::Float, 2, VectorFileType::TXT, "|", 1);
    options->m_readOnlyMapped = true;
    {
        std::ofstream out(input.path);
        out << "first\t1|2\nsecond\t3|4\n";
    }
    auto reader = Helper::VectorSetReader::CreateInstance(options);
    BOOST_REQUIRE(reader->LoadFile(input.path) == ErrorCode::Success);
    auto vectors = reader->GetVectorSet(0, 1);
    BOOST_CHECK_EQUAL(reader->SourceCount(), 2);
    BOOST_CHECK_EQUAL(vectors->Count(), 1);
    BOOST_CHECK_EQUAL(vectors->Dimension(), 2);
    BOOST_CHECK_EQUAL(static_cast<float*>(vectors->GetData())[1], 2.0f);
    reader.reset();
    BOOST_CHECK_EQUAL(static_cast<float*>(vectors->GetData())[0], 1.0f);
    {
        std::ofstream out(input.path);
        out << "wrong\t1|2|3\n";
    }
    BOOST_CHECK(Helper::VectorSetReader::CreateInstance(options)->LoadFile(input.path) != ErrorCode::Success);
}

BOOST_AUTO_TEST_CASE(NativeNormalizationFlagDoesNotMutateMappedInput)
{
    NativeInput input;
    input.Write(1, 2, "ab");
    for (bool normalized : {false, true}) {
        auto options = std::make_shared<Helper::ReaderOptions>(
            VectorValueType::UInt8, 2, VectorFileType::DEFAULT, "|", 1, normalized);
        options->m_readOnlyMapped = true;
        auto reader = Helper::VectorSetReader::CreateInstance(options);
        BOOST_REQUIRE(reader->LoadFile(input.path) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(reader->IsNormalized(), normalized);
        const auto vectors = reader->GetVectorSet();
        BOOST_CHECK_EQUAL(static_cast<char*>(vectors->GetData())[0], 'a');
        BOOST_CHECK_EQUAL(static_cast<char*>(vectors->GetData())[1], 'b');
    }
}

BOOST_AUTO_TEST_CASE(BillionRowMemoryViewUsesWideByteArithmeticWithoutCorpusAllocation)
{
    if (sizeof(std::size_t) < 8) return;
    struct RestoreIO {
        decltype(f_createIO) factory;
        ~RestoreIO() { f_createIO = factory; }
    } restore{f_createIO};
    f_createIO = []() -> std::shared_ptr<Helper::DiskIO> { return std::make_shared<CountingVectorIO>(); };
    // Shape-only fixture: the counting sink never dereferences or writes the payload.
    std::uint8_t firstRow[128] = {};
    auto source = std::make_shared<BasicVectorSet>(
        ByteArray(firstRow, sizeof(firstRow), false), VectorValueType::UInt8, 128, 1000000000);
    auto options = std::make_shared<Helper::ReaderOptions>(
        VectorValueType::UInt8, 128, VectorFileType::DEFAULT);
    Helper::MemoryVectorReader reader(options, source);
    const auto vectors = reader.GetVectorSet();
    BOOST_CHECK_EQUAL(vectors->Count(), 1000000000);
    BOOST_REQUIRE(vectors->Save("counting-sink-no-file") == ErrorCode::Success);
    BOOST_CHECK_EQUAL(countedVectorBytes, 128000000000ULL);
}

BOOST_AUTO_TEST_CASE(NativeTagsZeroBasedOwnedMapAndReadFallback)
{
    NativeInput input;
    const std::uint32_t values[] = {7, 100, 8, 200, 9, 300};
    std::ofstream(input.path, std::ios::binary | std::ios::trunc).write(
        reinterpret_cast<const char*>(values), sizeof(values));
    struct ReadOnlyFallback : Helper::SimpleFileIO {
        std::shared_ptr<std::uint8_t> MapReadOnly(std::uint64_t) override { return {}; }
    };
    struct RestoreIO {
        decltype(f_createIO) factory = f_createIO;
        ~RestoreIO() { f_createIO = factory; }
    } restore;
    for (bool fallback : {false, true}) {
        if (fallback)
            f_createIO = []() -> std::shared_ptr<Helper::DiskIO> {
                return std::make_shared<ReadOnlyFallback>();
            };
        auto all = Helper::ReadNativeAttributes(input.path.c_str(), 3, 3, 2);
        auto prefix = Helper::ReadNativeAttributes(input.path.c_str(), 2, 3, 2);
        BOOST_CHECK_EQUAL(all.Length(), 24);
        BOOST_CHECK_EQUAL(prefix.Length(), 16);
        BOOST_CHECK_EQUAL(reinterpret_cast<const std::uint32_t*>(prefix.Data())[0], 7);
        BOOST_CHECK_EQUAL(reinterpret_cast<const std::uint32_t*>(prefix.Data())[3], 200);
        BOOST_CHECK_EQUAL(reinterpret_cast<const std::uint32_t*>(all.Data())[5], 300);
        BOOST_CHECK_THROW(Helper::ReadNativeAttributes(input.path.c_str(), 2, 4, 2), std::runtime_error);
        BOOST_CHECK_THROW(Helper::ReadNativeAttributes(input.path.c_str(), 4, 3, 2), std::runtime_error);
    }
    std::ofstream(input.path, std::ios::binary | std::ios::trunc).write(
        reinterpret_cast<const char*>(values), 16);
    BOOST_CHECK_EQUAL(Helper::ReadNativeAttributes(input.path.c_str(), 2, 3, 2).Length(), 16);
}

BOOST_AUTO_TEST_CASE(NativeTagsRejectCountStrideTruncationAndOverflow)
{
    NativeInput input;
    for (int bytes : {0, 1, 7, 8, 15, 17, 20, 23, 25, 32}) {
        std::ofstream(input.path, std::ios::binary | std::ios::trunc) << std::string(bytes, 'x');
        BOOST_CHECK_THROW(Helper::ReadNativeAttributes(input.path.c_str(), 2, 3, 2), std::runtime_error);
    }
    for (const auto& shape : {std::pair<std::uint64_t, std::uint64_t>{0,2}, {2,0},
                             {2147483648ULL,2}, {2,2147483648ULL}, {2,536870912},
                             {UINT64_MAX,UINT64_MAX}})
        BOOST_CHECK_THROW(Helper::NativeAttributeBytes(shape.first, shape.second), std::runtime_error);
    if (sizeof(std::size_t) >= 8)
        BOOST_CHECK_EQUAL(Helper::NativeAttributeBytes(1000000000, 2), 8000000000ULL);
}

BOOST_AUTO_TEST_CASE(NpyPreparationRequiresExplicitShapeTypeAndExactPayload)
{
    NativeInput input;
    const auto write = [&](std::string header, std::size_t bytes = 24) {
        header.append((16 - ((10 + header.size() + 1) % 16)) % 16, ' ');
        header += '\n';
        std::ofstream file(input.path, std::ios::binary | std::ios::trunc);
        file.write("\x93NUMPY\x01\x00", 8);
        const unsigned char length[2] = {
            static_cast<unsigned char>(header.size() & 255),
            static_cast<unsigned char>(header.size() >> 8)};
        file.write(reinterpret_cast<const char*>(length), 2);
        file << header << std::string(bytes, '\0');
    };
    const std::string valid = "{'descr': '<u4', 'fortran_order': False, 'shape': (3, 2), }";
    write(valid);
    auto values = Helper::ReadNpyAttributes(input.path.c_str(), 2, false);
    BOOST_CHECK_EQUAL(values.rows, 3);
    BOOST_CHECK_EQUAL(values.data.Length(), 24);
    BOOST_CHECK_THROW(Helper::ReadNativeAttributes(input.path.c_str(), 3, 3, 2), std::runtime_error);
    BOOST_CHECK_THROW(Helper::ReadNpyAttributes(input.path.c_str(), 1, false), std::runtime_error);
    BOOST_CHECK_THROW(Helper::ReadNpyAttributes(input.path.c_str(), 1, true), std::runtime_error);
    for (const auto& header : {
        "{'descr': '<f4', 'fortran_order': False, 'shape': (3, 2), }",
        "{'descr': '>u4', 'fortran_order': False, 'shape': (3, 2), }",
        "{'descr': '<u4', 'fortran_order': True, 'shape': (3, 2), }",
        "{'descr': '<u4', 'fortran_order': False, 'shape': (6,), }",
        "{'descr': '<u4', 'fortran_order': False, 'shape': (3, 2), 'shape': (3, 2), }",
        "{'descr': '<u4' 'fortran_order': False, 'shape': (3, 2), }",
        "{'descr': '<u4', 'fortran_order': False, 'shape': (2147483648, 2), }",
        "{'descr': '<u4', 'fortran_order': False, 'shape': (3, 2), 'unknown': True, }"}) {
        write(header);
        BOOST_CHECK_THROW(Helper::ReadNpyAttributes(input.path.c_str(), 2, false), std::runtime_error);
    }
    for (std::size_t bytes : {0, 23, 25}) {
        write(valid, bytes);
        BOOST_CHECK_THROW(Helper::ReadNpyAttributes(input.path.c_str(), 2, false), std::runtime_error);
    }
    write("{'shape': (3,), 'descr': '<i4', 'fortran_order': False, }", 12);
    BOOST_CHECK_EQUAL(Helper::ReadNpyAttributes(input.path.c_str(), 1, true).rows, 3);
}

BOOST_AUTO_TEST_SUITE_END()

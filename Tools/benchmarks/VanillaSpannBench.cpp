// Fixed SIFT1M adapter for unmodified Microsoft SPANN 2ac3ebc.
// Build only through vanilla_native/CMakeLists.txt, against the original library.
// Marker payloads are JSON. VANILLA_IDS is emitted once per (nprobe, repeat, mode),
// in query order, with zero-based repeat and query_offset. Only mode=plain is a
// throughput measurement; profile uses the official two-phase native API.
// Require exit status 0 and VANILLA_DONE before accepting the complete comparison.

#include "inc/Core/SPANN/Index.h"
#include "inc/Core/Common/TruthSet.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Helper/VectorSetReader.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <locale>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using SPTAG::ErrorCode;
using SPTAG::Helper::IniReader;
using SPTAG::QueryResult;
using SPTAG::SizeType;
using NativeIndex = SPTAG::SPANN::Index<float>;
constexpr int kTopK = 10;
constexpr int kBaseCount = 1000000;
constexpr int kQueryRows = 10000;
constexpr int kTruthWidth = 100;
constexpr const char* kRevision = "2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b";

[[noreturn]] void Fail(const std::string& message) { throw std::runtime_error(message); }
void Require(bool condition, const std::string& message) { if (!condition) Fail(message); }
void NativeOK(ErrorCode code, const std::string& operation)
{
    if (code != ErrorCode::Success)
        Fail(operation + ": native ErrorCode=" + std::to_string(static_cast<int>(code)));
}

std::string Trim(const std::string& value)
{
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    return value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
}

std::string Lower(std::string value)
{
    for (char& c : value) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    return value;
}

std::string Json(const std::string& value)
{
    std::string out = "\"";
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 0x20) {
            out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15];
        } else out += static_cast<char>(c);
    }
    return out + '"';
}

std::string Required(const IniReader& ini, const std::string& section, const std::string& key)
{
    Require(ini.DoesParameterExist(section, key), "Missing [" + section + "] " + key);
    const auto value = Trim(ini.GetParameter(section, key, std::string()));
    Require(!value.empty(), "Empty [" + section + "] " + key);
    return value;
}

int Integer(const std::string& value, const std::string& name)
{
    int result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    Require(parsed.ec == std::errc() && parsed.ptr == value.data() + value.size(),
            "Invalid integer " + name + "=" + Json(value));
    return result;
}

int RequiredInt(const IniReader& ini, const std::string& section, const std::string& key)
{
    return Integer(Required(ini, section, key), "[" + section + "] " + key);
}

bool Boolean(const std::string& value, const std::string& name)
{
    const auto lower = Lower(value);
    if (lower == "true") return true;
    if (lower == "false") return false;
    Fail("Invalid boolean " + name + "=" + Json(value));
}

bool RequiredBool(const IniReader& ini, const std::string& section, const std::string& key)
{
    return Boolean(Required(ini, section, key), "[" + section + "] " + key);
}

void Equal(int actual, int expected, const std::string& name)
{
    Require(actual == expected, name + " must be " + std::to_string(expected) +
            "; got " + std::to_string(actual));
}

void Word(const IniReader& ini, const std::string& section, const std::string& key,
          const std::string& expected)
{
    Require(Lower(Required(ini, section, key)) == Lower(expected),
            "Only [" + section + "] " + key + "=" + expected + " is supported");
}

void ReadIni(IniReader& ini, const std::string& path)
{
    NativeOK(ini.LoadIniFile(path), "Load INI " + path);
}

template<class T>
T NativeEnum(const IniReader& ini, const std::string& section, const std::string& key)
{
    T value;
    const auto raw = Required(ini, section, key);
    Require(SPTAG::Helper::Convert::ConvertStringTo<T>(raw.c_str(), value),
            "Native reader rejected [" + section + "] " + key + "=" + Json(raw));
    return value;
}

using Schema = std::map<std::string, std::set<std::string>>;
void CheckSchema(const IniReader& ini, const std::string& path, const Schema& schema,
                 bool allowLauncherSection = false)
{
    Require(ini.GetParameters("").empty(), "INI parameters must have a section: " + path);
    // IniReader exposes keys but not section names. This pass checks only section
    // names/NUL bytes; every setting value still comes from the native reader.
    std::ifstream stream(path);
    Require(stream.good(), "Cannot inspect INI schema: " + path);
    for (std::string line; std::getline(stream, line);) {
        Require(line.find('\0') == std::string::npos, "NUL byte in INI: " + path);
        line = Trim(line);
        if (!line.empty() && line.front() == '[') {
            const auto section = Lower(Trim(line.substr(1, line.size() - 2)));
            Require(schema.count(section) != 0 || (allowLauncherSection && section == "execution"),
                    "Unsupported INI section [" + section + "]: " + path);
        }
    }
    Require(stream.eof(), "Failed reading INI schema: " + path);
    for (const auto& section : schema) {
        Require(ini.DoesSectionExist(section.first), "Missing section [" + section.first + "]: " + path);
        for (const auto& kv : ini.GetParameters(section.first))
            Require(section.second.count(kv.first) != 0,
                    "Unsupported [" + section.first + "] " + kv.first + ": " + path);
        for (const auto& key : section.second) (void)Required(ini, section.first, key);
    }
}

std::string AbsoluteInput(const std::string& value, const std::string& name)
{
    Require(fs::path(value).is_absolute() && value.find(',') == std::string::npos &&
            value.find('\0') == std::string::npos, name + " must be a single absolute path");
    Require(fs::exists(value), name + " does not exist: " + value);
    return fs::canonical(value).string();
}

struct SearchCase {
    std::string path;
    IniReader ini;
    int nprobe, threads, hashExponent, resultNum, maxCheck, pages;
    float maxDistRatio;
};

struct Config {
    std::string path, indexDirectory, queryPath, truthPath;
    SPTAG::VectorValueType valueType;
    SPTAG::DistCalcMethod distance;
    SPTAG::VectorFileType queryType;
    SPTAG::TruthFileType truthType;
    int dim, count, warmup, offset, repeats;
    bool expectedDirectIO;
    std::vector<SearchCase> cases;
};

Config ReadConfig(const std::string& path)
{
    Config c;
    c.path = fs::absolute(path).string();
    IniReader ini;
    ReadIni(ini, c.path);
    // [Execution] belongs to the parent launcher; none of its values are used here.
    CheckSchema(ini, c.path, {
        {"base", {"valuetype", "distcalcmethod", "dim", "indexdirectory", "querypath",
                  "querytype", "truthpath", "truthtype"}},
        {"benchmark", {"querycount", "warmup", "measureoffset", "repeats", "searchconfigs",
                       "expecteddirectio"}}
    }, true);
    Word(ini, "Base", "ValueType", "Float");
    Word(ini, "Base", "DistCalcMethod", "L2");
    Word(ini, "Base", "QueryType", "XVEC");
    Word(ini, "Base", "TruthType", "XVEC");
    c.valueType = NativeEnum<SPTAG::VectorValueType>(ini, "Base", "ValueType");
    c.distance = NativeEnum<SPTAG::DistCalcMethod>(ini, "Base", "DistCalcMethod");
    c.queryType = NativeEnum<SPTAG::VectorFileType>(ini, "Base", "QueryType");
    c.truthType = NativeEnum<SPTAG::TruthFileType>(ini, "Base", "TruthType");
    Require(c.valueType == SPTAG::VectorValueType::Float && c.distance == SPTAG::DistCalcMethod::L2 &&
            c.queryType == SPTAG::VectorFileType::XVEC && c.truthType == SPTAG::TruthFileType::XVEC,
            "Native reader rejected the declared Float/L2/XVEC types");
    c.dim = RequiredInt(ini, "Base", "Dim");
    c.count = RequiredInt(ini, "Benchmark", "QueryCount");
    c.warmup = RequiredInt(ini, "Benchmark", "Warmup");
    c.offset = RequiredInt(ini, "Benchmark", "MeasureOffset");
    c.repeats = RequiredInt(ini, "Benchmark", "Repeats");
    c.expectedDirectIO = RequiredBool(ini, "Benchmark", "ExpectedDirectIO");
    Equal(c.dim, 128, "Dim");
    Equal(c.count, 1000, "QueryCount");
    Equal(c.warmup, 1000, "Warmup");
    Equal(c.offset, 0, "MeasureOffset");
    Equal(c.repeats, 3, "Repeats");
    Require(c.expectedDirectIO, "Original STATIC is O_DIRECT; ExpectedDirectIO=false is unsupported");
    c.indexDirectory = AbsoluteInput(Required(ini, "Base", "IndexDirectory"), "IndexDirectory");
    c.queryPath = AbsoluteInput(Required(ini, "Base", "QueryPath"), "QueryPath");
    c.truthPath = AbsoluteInput(Required(ini, "Base", "TruthPath"), "TruthPath");

    const auto paths = Required(ini, "Benchmark", "SearchConfigs");
    std::set<std::string> seenPaths;
    std::set<int> probes;
    for (std::size_t begin = 0; begin <= paths.size();) {
        auto end = paths.find(',', begin);
        if (end == std::string::npos) end = paths.size();
        SearchCase p;
        p.path = AbsoluteInput(Trim(paths.substr(begin, end - begin)), "SearchConfigs entry");
        Require(seenPaths.insert(p.path).second, "Repeated search INI: " + p.path);
        ReadIni(p.ini, p.path);
        CheckSchema(p.ini, p.path, {{"searchssdindex", {
            "isexecute", "buildssdindex", "internalresultnum", "numberofthreads", "hashtableexponent",
            "resultnum", "maxcheck", "maxdistratio", "searchpostingpagelimit"}}});
        Require(RequiredBool(p.ini, "SearchSSDIndex", "isExecute"), "Search isExecute must be true");
        Require(!RequiredBool(p.ini, "SearchSSDIndex", "BuildSsdIndex"), "BuildSsdIndex must be false");
        p.nprobe = RequiredInt(p.ini, "SearchSSDIndex", "InternalResultNum");
        p.threads = RequiredInt(p.ini, "SearchSSDIndex", "NumberOfThreads");
        p.hashExponent = RequiredInt(p.ini, "SearchSSDIndex", "HashTableExponent");
        p.resultNum = RequiredInt(p.ini, "SearchSSDIndex", "ResultNum");
        p.maxCheck = RequiredInt(p.ini, "SearchSSDIndex", "MaxCheck");
        p.pages = RequiredInt(p.ini, "SearchSSDIndex", "SearchPostingPageLimit");
        const auto ratio = Required(p.ini, "SearchSSDIndex", "MaxDistRatio");
        const auto parsed = std::from_chars(ratio.data(), ratio.data() + ratio.size(), p.maxDistRatio);
        Require(parsed.ec == std::errc() && parsed.ptr == ratio.data() + ratio.size() &&
                std::isfinite(p.maxDistRatio) && p.maxDistRatio == 8.0f, "MaxDistRatio must be exactly 8");
        Equal(p.threads, 1, "NumberOfThreads");
        Equal(p.hashExponent, 4, "HashTableExponent");
        Equal(p.resultNum, kTopK, "ResultNum");
        Equal(p.maxCheck, 2048, "MaxCheck");
        Require(p.pages == 12 || p.pages == 15, "SearchPostingPageLimit must explicitly declare 12 or 15");
        Require(probes.insert(p.nprobe).second, "Repeated InternalResultNum: " + std::to_string(p.nprobe));
        c.cases.push_back(std::move(p));
        if (end == paths.size()) break;
        begin = end + 1;
    }
    Require(probes == std::set<int>({16, 24, 32, 48, 64}),
            "SearchConfigs must contain exactly one case for each L=16,24,32,48,64");
    for (const auto& p : c.cases)
        Equal(p.pages, c.cases.front().pages, "SearchPostingPageLimit across cases");
    return c;
}

void ValidateSavedIndex(const Config& c)
{
    IniReader saved;
    ReadIni(saved, c.indexDirectory + "/indexloader.ini");
    Word(saved, "Index", "IndexAlgoType", "SPANN");
    Word(saved, "Index", "ValueType", "Float");
    Word(saved, "Base", "IndexAlgoType", "BKT");
    Word(saved, "Base", "ValueType", "Float");
    Word(saved, "Base", "DistCalcMethod", "L2");
    Equal(RequiredInt(saved, "Base", "Dim"), c.dim, "Saved Dim");
    Word(saved, "BuildSSDIndex", "Storage", "STATIC");
    Word(saved, "Base", "SSDIndex", "SPTAGFullList.bin");
    Equal(RequiredInt(saved, "Base", "SSDIndexFileNum"), 1, "Saved SSDIndexFileNum");
    Require(!saved.DoesSectionExist("Quantizer") &&
            Trim(saved.GetParameter("Base", "QuantizerFilePath", std::string())).empty(),
            "Quantized indices are unsupported");
    Require(Trim(saved.GetParameter("BuildSSDIndex", "PersistentBufferPath", std::string())).empty(),
            "A persistent update buffer is unsupported in this read-only adapter");
    for (const char* key : {"Recovery", "EnableADC", "EnableDeltaEncoding",
                            "EnablePostingListRearrange", "EnableDataCompression"}) {
        if (saved.DoesParameterExist("BuildSSDIndex", key))
            Require(!RequiredBool(saved, "BuildSSDIndex", key), std::string("Unsupported saved setting: ") + key);
    }
    Require(RequiredInt(saved, "BuildSSDIndex", "SearchInternalResultNum") >= 64,
            "Saved SearchInternalResultNum must reserve at least 64 native AIO slots");
    const int savedPages = RequiredInt(saved, "BuildSSDIndex", "SearchPostingPageLimit");
    const int vectorLimit = RequiredInt(saved, "BuildSSDIndex", "PostingVectorLimit");
    Require(savedPages > 0 && vectorLimit >= 0, "Invalid saved posting limits");
    const auto uplift = (static_cast<std::int64_t>(vectorLimit) * (c.dim * sizeof(float) + sizeof(int)) +
                         SPTAG::PageSize - 1) / SPTAG::PageSize;
    Require(std::max<std::int64_t>(savedPages, uplift) == c.cases.front().pages,
            "Saved load-time posting cap differs from SearchPostingPageLimit=" +
            std::to_string(c.cases.front().pages) + " "
            "(including PostingVectorLimit uplift); changing an option after LoadIndex cannot repair this");
    Require(!fs::exists(c.indexDirectory + "/SPTAGFullList.bin_1"), "Split posting files are unsupported");
}

void VerifyCase(NativeIndex& index, const SearchCase& p)
{
    auto head = index.GetMemoryIndex();
    const auto& o = *index.GetOptions();
    Require(o.m_enableSSD && !o.m_buildSsdIndex && o.m_searchInternalResultNum == p.nprobe &&
            o.m_iSSDNumberOfThreads == p.threads && o.m_hashExp == p.hashExponent &&
            o.m_resultNum == p.resultNum && o.m_maxCheck == p.maxCheck &&
            o.m_maxDistRatio == p.maxDistRatio && o.m_searchPostingPageLimit == p.pages,
            "Native SPANN options did not accept the search overlay: " + p.path);
    Equal(Integer(head->GetParameter("MaxCheck"), "effective memory MaxCheck"), p.maxCheck, "Memory MaxCheck");
    Equal(Integer(head->GetParameter("HashTableExponent"), "effective memory HashTableExponent"),
          p.hashExponent, "Memory HashTableExponent");
    Equal(Integer(head->GetParameter("NumberOfThreads"), "effective memory NumberOfThreads"),
          p.threads, "Memory NumberOfThreads");
}

void ApplyCase(NativeIndex& index, const SearchCase& p)
{
    // SSDServing/main.cpp maps this section into BuildSSDIndex and renames L.
    for (const auto& kv : p.ini.GetParameters("SearchSSDIndex")) {
        const auto key = kv.first == "internalresultnum" ? "SearchInternalResultNum" : kv.first;
        const auto value = Trim(kv.second);
        NativeOK(index.SetParameter(key.c_str(), value.c_str(), "BuildSSDIndex"), "Set native " + key);
    }
    auto head = index.GetMemoryIndex();
    // SPANN::UpdateIndex only forwards NumberOfThreads in the pinned source.
    for (const char* key : {"MaxCheck", "HashTableExponent", "NumberOfThreads"}) {
        const auto value = Required(p.ini, "SearchSSDIndex", key);
        NativeOK(head->SetParameter(key, value.c_str()), std::string("Set memory ") + key);
    }
    NativeOK(head->UpdateIndex(), "Memory UpdateIndex");
    NativeOK(index.UpdateIndex(), "SPANN UpdateIndex");
    VerifyCase(index, p);
}

struct DirectFD { int fd; unsigned long flags; std::string path, octal; };
std::vector<DirectFD> InspectDirectIO(const Config& c)
{
    const auto path = fs::canonical(c.indexDirectory + "/SPTAGFullList.bin");
    struct stat expected {};
    Require(::stat(path.c_str(), &expected) == 0, "Cannot stat native posting file");
    std::vector<DirectFD> result;
    for (const auto& entry : fs::directory_iterator("/proc/self/fd")) {
        const auto name = entry.path().filename().string();
        int fd = -1;
        const auto parsed = std::from_chars(name.data(), name.data() + name.size(), fd);
        if (parsed.ec != std::errc() || parsed.ptr != name.data() + name.size()) continue;
        struct stat actual {};
        if (::fstat(fd, &actual) != 0 || actual.st_dev != expected.st_dev || actual.st_ino != expected.st_ino)
            continue;
        std::ifstream info("/proc/self/fdinfo/" + name);
        std::optional<unsigned long> flags;
        std::string octal;
        for (std::string line; std::getline(info, line);) {
            if (line.rfind("flags:", 0) != 0) continue;
            octal = Trim(line.substr(6));
            unsigned long value = 0;
            const auto flagResult = std::from_chars(octal.data(), octal.data() + octal.size(), value, 8);
            Require(flagResult.ec == std::errc() && flagResult.ptr == octal.data() + octal.size(),
                    "Malformed /proc/self/fdinfo flags for native posting fd");
            flags = value;
        }
        Require(flags.has_value(), "Cannot read native posting fdinfo flags");
        const int fcntlFlags = ::fcntl(fd, F_GETFL);
        Require(fcntlFlags >= 0 && ((*flags & O_DIRECT) != 0) == c.expectedDirectIO &&
                ((fcntlFlags & O_DIRECT) != 0) == c.expectedDirectIO,
                "Actual native posting O_DIRECT differs from ExpectedDirectIO");
        result.push_back({fd, *flags, fs::read_symlink(entry.path()).string(), octal});
    }
    Require(!result.empty(), "No open SPTAGFullList.bin fd found; native IO mode is unverified");
    return result;
}

void CheckXvecSize(const std::string& path, int width)
{
    static_assert(sizeof(float) == 4 && sizeof(SizeType) == 4, "This adapter requires native 32-bit XVEC");
    const auto expected = static_cast<std::uintmax_t>(kQueryRows) * (width + 1) * 4;
    Require(fs::is_regular_file(path) && fs::file_size(path) == expected,
            "Expected exactly 10000 XVEC rows of width " + std::to_string(width) + ": " + path);
}

class ReaderDirectory {
    fs::path original = fs::current_path();
    fs::path owned = original / ("vanillaspannbench-reader-" + std::to_string(::getpid()));
public:
    ReaderDirectory()
    {
        Require(fs::create_directory(owned), "Refusing to reuse a native reader staging directory: " + owned.string());
        fs::current_path(owned);
    }
    ~ReaderDirectory()
    {
        std::error_code ignored;
        fs::current_path(original, ignored);
        // Native XvecVectorReader deletes its own staged vector file on destruction.
        fs::remove(owned / "tempfolder", ignored);
        fs::remove(owned, ignored);
    }
};

std::shared_ptr<SPTAG::VectorSet> LoadQueries(const Config& c)
{
    CheckXvecSize(c.queryPath, c.dim);
    // The native XVEC reader stages a file under cwd. Give it a new project-local
    // directory so its random filename cannot overwrite anyone else's file.
    ReaderDirectory directory;
    auto options = std::make_shared<SPTAG::Helper::ReaderOptions>(
        c.valueType, c.dim, c.queryType, "|", c.cases.front().threads, false);
    auto reader = SPTAG::Helper::VectorSetReader::CreateInstance(options);
    Require(reader != nullptr, "Native query reader is unavailable");
    NativeOK(reader->LoadFile(c.queryPath), "Native query LoadFile");
    auto queries = reader->GetVectorSet();
    Require(queries && queries->Available() && queries->Dimension() == c.dim &&
            queries->GetValueType() == c.valueType && queries->Count() == kQueryRows,
            "Native query reader returned an unexpected shape/type");
    const auto values = static_cast<const float*>(queries->GetData());
    for (std::size_t i = 0; i < static_cast<std::size_t>(queries->Count()) * c.dim; ++i)
        Require(std::isfinite(values[i]), "Query data contains a nonfinite float");
    return queries;
}

std::vector<std::set<SizeType>> LoadTruth(const Config& c)
{
    CheckXvecSize(c.truthPath, kTruthWidth);
    auto io = SPTAG::f_createIO();
    Require(io && io->Initialize(c.truthPath.c_str(), std::ios::in | std::ios::binary),
            "Cannot open groundtruth through native f_createIO");
    // Validate every width before the native loader can allocate from it or exit().
    for (int row = 0; row < kQueryRows; ++row) {
        std::int32_t width = 0;
        const auto offset = static_cast<std::uint64_t>(row) * (kTruthWidth + 1) * 4;
        Require(io->ReadBinary(4, reinterpret_cast<char*>(&width), offset) == 4 && width == kTruthWidth,
                "Groundtruth XVEC width is not 100 at row " + std::to_string(row));
    }
    Require(io->Initialize(c.truthPath.c_str(), std::ios::in | std::ios::binary),
            "Cannot rewind native groundtruth reader");
    std::vector<std::set<SizeType>> truth;
    SizeType rows = kQueryRows;
    int originalK = 0;
    SPTAG::COMMON::TruthSet::LoadTruth(io, truth, rows, originalK, kTopK, c.truthType);
    Equal(originalK, kTruthWidth, "Groundtruth width");
    Equal(rows, kQueryRows, "Groundtruth rows");
    for (int row = 0; row < rows; ++row) {
        Require(truth[row].size() == kTopK, "Groundtruth top10 contains duplicate IDs at row " + std::to_string(row));
        for (const auto id : truth[row])
            Require(id >= 0 && id < kBaseCount, "Groundtruth ID outside SIFT1M at row " + std::to_string(row));
    }
    return truth;
}

struct Observation {
    double totalMs = 0, headMs = 0, postingMs = 0;
    int headScanned = -1;
    SPTAG::SPANN::SearchStats stats;
    ErrorCode headCode = ErrorCode::Success, diskCode = ErrorCode::Success;
    Observation()
    {
        stats.m_totalListElementsCount = stats.m_diskIOCount = stats.m_diskAccessCount = -1;
    }
};
struct Group {
    std::vector<QueryResult> results;
    std::vector<Observation> observations;
    Group(const SPTAG::VectorSet& queries, int offset, int count, int k) : observations(count)
    {
        results.reserve(count);
        for (int q = 0; q < count; ++q) {
            results.emplace_back(queries.GetVector(offset + q), k, false);
            results.back().Reset();
            results.back().SetScanned(-1);
        }
    }
};
struct Snapshot {
    std::array<SizeType, kTopK> ids;
    std::array<std::uint32_t, kTopK> distanceBits;
    int scanned, headScanned, listElements, diskIO, diskAccess;
};
struct RunResult {
    std::vector<Snapshot> rows;
    double totalMs = 0, headMs = 0, postingMs = 0, windowMs = 0, recall = 0;
    double scanned = 0, headScanned = 0, listElements = 0, diskIO = 0, diskAccess = 0;
};

double Milliseconds(Clock::time_point begin, Clock::time_point end)
{
    return std::chrono::duration<double, std::milli>(end - begin).count();
}
const char* Mode(bool profile) { return profile ? "profile" : "plain"; }
std::string PointContext(const SearchCase& p, int repeat, bool profile)
{
    return "nprobe=" + std::to_string(p.nprobe) + " repeat=" + std::to_string(repeat) +
           " mode=" + Mode(profile);
}
std::string PointFields(const SearchCase& p, int repeat, bool profile)
{
    return "\"nprobe\":" + std::to_string(p.nprobe) + ",\"repeat\":" + std::to_string(repeat) +
           ",\"mode\":" + Json(Mode(profile));
}

void WindowMarker(const SearchCase& p, int repeat, bool profile, const char* phase,
                  const char* event, int offset, int count, int completed, bool ok)
{
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
    std::cout << "VANILLA_WINDOW {" << PointFields(p, repeat, profile) << ",\"pid\":" << ::getpid()
              << ",\"phase\":" << Json(phase) << ",\"event\":" << Json(event)
              << ",\"query_offset\":" << offset << ",\"count\":" << count
              << ",\"completed\":" << completed << ",\"ok\":" << (ok ? "true" : "false")
              << ",\"steady_ns\":" << ns << "}\n" << std::flush;
}

void SearchOne(NativeIndex& index, SPTAG::VectorIndex& head, QueryResult& outer,
               Observation& o, int nprobe, bool profile)
{
    if (!profile) {
        const auto begin = Clock::now();
        o.headCode = index.SearchIndex(outer);
        const auto end = Clock::now();
        o.totalMs = Milliseconds(begin, end);
        return;
    }
    Clock::time_point headBegin, headEnd, postingBegin, postingEnd;
    const auto begin = Clock::now();
    {
        auto inner = std::make_unique<SPTAG::COMMON::QueryResultSet<float>>(
            static_cast<const float*>(outer.GetTarget()), nprobe);
        headBegin = Clock::now();
        o.headCode = head.SearchIndex(*inner);
        headEnd = Clock::now();
        o.headScanned = inner->GetScanned();
        postingBegin = Clock::now();
        if (o.headCode == ErrorCode::Success) o.diskCode = index.SearchDiskIndex(*inner, &o.stats);
        postingEnd = Clock::now();
        std::copy(inner->GetResults(), inner->GetResults() + outer.GetResultNum(), outer.GetResults());
        outer.SetScanned(inner->GetScanned());
    } // Match ordinary SearchIndex: allocation, top10 copy and deletion are timed.
    const auto end = Clock::now();
    o.totalMs = Milliseconds(begin, end);
    o.headMs = Milliseconds(headBegin, headEnd);
    o.postingMs = Milliseconds(postingBegin, postingEnd);
}

double ExecuteWindow(NativeIndex& index, SPTAG::VectorIndex& head, Group& group,
                     const SearchCase& p, int repeat, bool profile, const char* phase, int offset)
{
    const int count = static_cast<int>(group.results.size());
    WindowMarker(p, repeat, profile, phase, "begin", offset, count, 0, true);
    int completed = 0;
    const auto begin = Clock::now();
    try {
        for (; completed < count; ++completed) {
            auto& observation = group.observations[completed];
            SearchOne(index, head, group.results[completed], observation, p.nprobe, profile);
            if (observation.headCode != ErrorCode::Success || observation.diskCode != ErrorCode::Success) {
                const auto where = PointContext(p, repeat, profile) + " phase=" + phase +
                                   " query=" + std::to_string(offset + completed);
                NativeOK(observation.headCode, where + (profile ? " memory SearchIndex" : " SearchIndex"));
                NativeOK(observation.diskCode, where + " SearchDiskIndex");
            }
        }
    } catch (...) {
        WindowMarker(p, repeat, profile, phase, "end", offset, count, completed, false);
        throw;
    }
    const auto end = Clock::now();
    WindowMarker(p, repeat, profile, phase, "end", offset, count, completed, true);
    return Milliseconds(begin, end);
}

Snapshot ValidateResult(QueryResult& result, const Observation& observation, bool profile,
                        const std::string& where)
{
    Snapshot snapshot {};
    for (int rank = 0; rank < kTopK; ++rank) {
        const auto& r = *result.GetResult(rank);
        const auto position = where + " rank=" + std::to_string(rank);
        Require(r.VID >= 0 && r.VID < kBaseCount && std::isfinite(r.Dist) &&
                r.Dist >= 0 && r.Dist < SPTAG::MaxDist,
                "Invalid result at " + position + " id=" + std::to_string(r.VID) +
                " distance=" + std::to_string(r.Dist));
        if (rank > 0) Require(result.GetResult(rank - 1)->Dist <= r.Dist, "Unsorted result at " + position);
        for (int previous = 0; previous < rank; ++previous)
            Require(snapshot.ids[previous] != r.VID, "Duplicate result ID at " + position);
        snapshot.ids[rank] = r.VID;
        std::memcpy(&snapshot.distanceBits[rank], &r.Dist, sizeof(r.Dist));
    }
    snapshot.scanned = result.GetScanned();
    snapshot.headScanned = observation.headScanned;
    snapshot.listElements = observation.stats.m_totalListElementsCount;
    snapshot.diskIO = observation.stats.m_diskIOCount;
    snapshot.diskAccess = observation.stats.m_diskAccessCount;
    Require(snapshot.scanned >= 0, "GetScanned was not populated: " + where);
    if (profile) {
        Require(snapshot.headScanned >= 0 && snapshot.listElements >= 0 &&
                snapshot.diskIO >= 0 && snapshot.diskAccess >= 0,
                "Native profiling counters were not populated: " + where);
        Require(snapshot.scanned == snapshot.listElements,
                "STATIC GetScanned != m_totalListElementsCount: " + where);
        Require(std::isfinite(observation.headMs) && observation.headMs >= 0 &&
                std::isfinite(observation.postingMs) && observation.postingMs >= 0,
                "Invalid phase duration: " + where);
    }
    Require(std::isfinite(observation.totalMs) && observation.totalMs > 0, "Invalid timed duration: " + where);
    return snapshot;
}

RunResult Run(NativeIndex& index, const SPTAG::VectorSet& queries,
              const std::vector<std::set<SizeType>>& truth, const Config& c,
              const SearchCase& p, int repeat, bool profile)
{
    Group warmup(queries, 0, c.warmup, p.resultNum);
    Group measured(queries, c.offset, c.count, p.resultNum);
    RunResult result;
    result.rows.resize(c.count);
    auto head = index.GetMemoryIndex();
    const auto context = PointContext(p, repeat, profile);
    ExecuteWindow(index, *head, warmup, p, repeat, profile, "warmup", 0);
    for (int q = 0; q < c.warmup; ++q)
        (void)ValidateResult(warmup.results[q], warmup.observations[q], profile,
                             context + " phase=warmup query=" + std::to_string(q));
    result.windowMs = ExecuteWindow(index, *head, measured, p, repeat, profile, "measure", c.offset);
    std::uint64_t hits = 0;
    for (int q = 0; q < c.count; ++q) {
        const auto& observation = measured.observations[q];
        auto& snapshot = result.rows[q];
        snapshot = ValidateResult(measured.results[q], observation, profile,
                                  context + " query=" + std::to_string(c.offset + q));
        for (const auto id : snapshot.ids) hits += truth[c.offset + q].count(id);
        result.totalMs += observation.totalMs;
        result.headMs += observation.headMs;
        result.postingMs += observation.postingMs;
        result.scanned += snapshot.scanned;
        if (profile) {
            result.headScanned += snapshot.headScanned;
            result.listElements += snapshot.listElements;
            result.diskIO += snapshot.diskIO;
            result.diskAccess += snapshot.diskAccess;
        }
    }
    result.recall = static_cast<double>(hits) / (c.count * p.resultNum);
    return result;
}

void Compare(const RunResult& actual, const RunResult& reference, const Config& c,
             const std::string& context, const char* referenceMode, bool profileCounters)
{
    for (std::size_t q = 0; q < actual.rows.size(); ++q) {
        const auto& a = actual.rows[q];
        const auto& b = reference.rows[q];
        const auto where = context + " reference=" + referenceMode +
                           " query=" + std::to_string(c.offset + q);
        for (int rank = 0; rank < kTopK; ++rank) {
            if (a.ids[rank] != b.ids[rank] || a.distanceBits[rank] != b.distanceBits[rank]) {
                Fail("Top10 mismatch " + where + " rank=" + std::to_string(rank) +
                     " actual_id=" + std::to_string(a.ids[rank]) + " reference_id=" + std::to_string(b.ids[rank]) +
                     " actual_float_bits=" + std::to_string(a.distanceBits[rank]) +
                     " reference_float_bits=" + std::to_string(b.distanceBits[rank]));
            }
        }
        Require(a.scanned == b.scanned, "GetScanned mismatch " + where +
                " actual=" + std::to_string(a.scanned) + " reference=" + std::to_string(b.scanned));
        if (profileCounters)
            Require(a.headScanned == b.headScanned && a.listElements == b.listElements &&
                    a.diskIO == b.diskIO && a.diskAccess == b.diskAccess,
                    "Native profile work mismatch " + where +
                    " head_scanned=" + std::to_string(a.headScanned) + "/" + std::to_string(b.headScanned) +
                    " list_elements=" + std::to_string(a.listElements) + "/" + std::to_string(b.listElements) +
                    " disk_io=" + std::to_string(a.diskIO) + "/" + std::to_string(b.diskIO) +
                    " disk_access=" + std::to_string(a.diskAccess) + "/" + std::to_string(b.diskAccess) +
                    " (actual/reference)");
    }
}

void EmitPoint(const RunResult& r, const Config& c, const SearchCase& p, int repeat, bool profile,
               bool sameCompared, bool otherCompared)
{
    const double count = c.count;
    auto observed = [profile, count](double value) {
        if (!profile) return std::string("null");
        std::ostringstream s;
        s << std::setprecision(17) << value / count;
        return s.str();
    };
    std::cout << "VANILLA_POINT {" << PointFields(p, repeat, profile)
              << ",\"search_config\":" << Json(p.path)
              << ",\"count\":" << c.count << ",\"warmup\":" << c.warmup << ",\"query_offset\":" << c.offset
              << ",\"result_num\":" << p.resultNum << ",\"number_of_threads\":" << p.threads
              << ",\"hash_table_exponent\":" << p.hashExponent << ",\"max_check\":" << p.maxCheck
              << ",\"memory_max_check\":" << p.maxCheck << ",\"memory_hash_table_exponent\":" << p.hashExponent
              << ",\"memory_number_of_threads\":" << p.threads << ",\"max_dist_ratio\":" << p.maxDistRatio
              << ",\"search_posting_page_limit\":" << p.pages << ",\"actual_direct_io\":true"
              << ",\"recall\":" << r.recall << ",\"total_ms\":" << r.totalMs
              << ",\"mean_ms\":" << r.totalMs / count << ",\"qps\":" << count * 1000.0 / r.totalMs
              << ",\"window_elapsed_ms\":" << r.windowMs << ",\"head_ms\":" << observed(r.headMs)
              << ",\"posting_ms\":" << observed(r.postingMs)
              << ",\"adapter_overhead_ms\":" << observed(r.totalMs - r.headMs - r.postingMs)
              << ",\"io_ms\":null,\"scan_ms\":null"
              << ",\"native_get_scanned_mean\":" << r.scanned / count
              << ",\"native_head_get_scanned_mean\":" << observed(r.headScanned)
              << ",\"native_m_totalListElementsCount_mean\":" << observed(r.listElements)
              << ",\"native_m_diskIOCount_mean\":" << observed(r.diskIO)
              << ",\"native_m_diskAccessCount_mean\":" << observed(r.diskAccess)
              << ",\"compared_same_mode\":" << (sameCompared ? "true" : "false")
              << ",\"compared_other_mode\":" << (otherCompared ? "true" : "false") << "}\n";
    std::cout << "VANILLA_IDS {" << PointFields(p, repeat, profile)
              << ",\"count\":" << c.count << ",\"query_offset\":" << c.offset << ",\"ids\":[";
    for (std::size_t q = 0; q < r.rows.size(); ++q) {
        if (q) std::cout << ',';
        std::cout << '[';
        for (int rank = 0; rank < kTopK; ++rank) {
            if (rank) std::cout << ',';
            std::cout << r.rows[q].ids[rank];
        }
        std::cout << ']';
    }
    std::cout << "]}\n" << std::flush;
}

void EmitReady(const Config& c, NativeIndex& index, const std::vector<DirectFD>& fds, int initialL)
{
    const auto& o = *index.GetOptions();
    std::cout << "VANILLA_READY {\"pid\":" << ::getpid() << ",\"revision\":" << Json(kRevision)
              << ",\"compiler\":" << Json(__VERSION__) << ",\"config\":" << Json(c.path)
              << ",\"index_directory\":" << Json(c.indexDirectory) << ",\"query_path\":" << Json(c.queryPath)
              << ",\"truth_path\":" << Json(c.truthPath) << ",\"base_count\":" << index.GetNumSamples()
              << ",\"head_count\":" << index.GetMemoryIndex()->GetNumSamples()
              << ",\"query_rows\":" << kQueryRows << ",\"truth_width\":" << kTruthWidth
              << ",\"dim\":" << c.dim << ",\"value_type\":\"Float\",\"distance\":\"L2\",\"storage\":\"STATIC\""
              << ",\"initial_search_internal_result_num\":" << initialL
              << ",\"current_search_internal_result_num\":" << o.m_searchInternalResultNum
              << ",\"calling_search_threads\":1"
              << ",\"memory_max_check\":" << index.GetMemoryIndex()->GetParameter("MaxCheck")
              << ",\"memory_hash_table_exponent\":" << index.GetMemoryIndex()->GetParameter("HashTableExponent")
              << ",\"memory_number_of_threads\":" << index.GetMemoryIndex()->GetParameter("NumberOfThreads")
              << ",\"loaded_search_posting_page_limit\":" << o.m_searchPostingPageLimit
              << ",\"saved_use_direct_io_option\":" << (o.m_useDirectIO ? "true" : "false")
              << ",\"expected_direct_io\":true,\"actual_direct_io\":true,\"posting_fds\":[";
    for (std::size_t i = 0; i < fds.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << "{\"fd\":" << fds[i].fd << ",\"path\":" << Json(fds[i].path)
                  << ",\"flags\":" << fds[i].flags << ",\"flags_octal\":" << Json(fds[i].octal)
                  << ",\"o_direct\":true}";
    }
    std::cout << "],\"counter_semantics\":{"
              << "\"native_get_scanned\":\"STATIC overwrites GetScanned with listElements after posting deduplication\","
              << "\"native_head_get_scanned\":\"BKT WorkSpace::m_iNumberOfCheckedLeaves before disk search\","
              << "\"m_totalListElementsCount\":\"sum of selected listEleCount minus ProcessPosting deduper rejections; not raw posting volume\","
              << "\"m_diskIOCount\":\"selected posting-list read requests, not physical device IOs\","
              << "\"m_diskAccessCount\":\"sum of requested listPageCount in 4096-byte pages, including alignment\","
              << "\"posting_ms\":\"native SearchDiskIndex including translation, posting selection, IO, scan and sort\","
              << "\"io_ms_scan_ms\":\"unavailable; original STATIC does not populate m_compLatency/m_diskReadLatency\"},"
              << "\"limitations\":[\"profile throughput is diagnostic, not ordinary throughput\","
              << "\"native SearchDiskIndex discards its nested return code; original async error propagation is unchanged\"]"
              << "}\n" << std::flush;
}
} // namespace

int main(int argc, char** argv)
{
    std::locale::global(std::locale::classic());
    std::cout << std::setprecision(17);
    try {
        Require(argc == 3 && std::string(argv[1]) == "--config",
                "Usage: vanillaspannbench --config <fixed INI>; no data/search CLI overrides are supported");
        const auto config = ReadConfig(argv[2]);
        ValidateSavedIndex(config);
        std::shared_ptr<SPTAG::VectorIndex> generic;
        NativeOK(SPTAG::VectorIndex::LoadIndex(config.indexDirectory, generic), "VectorIndex::LoadIndex");
        auto index = std::dynamic_pointer_cast<NativeIndex>(generic);
        Require(index && index->IsReady(), "Loaded index is not a ready native SPANN::Index<float>");
        Require(index->GetDistCalcMethod() == config.distance && index->GetOptions()->m_dim == config.dim,
                "Loaded SPANN distance/dimension differs from the benchmark INI");
        auto head = index->GetMemoryIndex();
        Require(head && head->IsReady() && head->GetIndexAlgoType() == SPTAG::IndexAlgoType::BKT &&
                head->GetVectorValueType() == config.valueType && head->GetDistCalcMethod() == config.distance &&
                head->GetFeatureDim() == config.dim && head->GetNumSamples() >= 64,
                "Expected a ready Float/L2/128 BKT head index with at least 64 heads");
        Require(index->GetDiskIndex() && index->GetDiskIndex()->Available() &&
                index->GetOptions()->m_storage == SPTAG::Storage::STATIC &&
                !index->GetQuantizer() && !head->GetQuantizer(), "Expected an available, unquantized STATIC disk index");
        Equal(index->GetNumSamples(), kBaseCount, "Native base count");
        Equal(index->GetNumDeleted(), 0, "Native deleted count");
        Equal(head->GetNumDeleted(), 0, "Native head deleted count");
        Equal(index->GetOptions()->m_searchPostingPageLimit, config.cases.front().pages, "Loaded posting page cap");
        const int initialL = index->GetOptions()->m_searchInternalResultNum;
        Require(initialL >= 64, "Loaded native AIO capacity is below the maximum requested L");
        const auto fds = InspectDirectIO(config);
        const auto queries = LoadQueries(config);
        const auto truth = LoadTruth(config);
        ApplyCase(*index, config.cases.front());
        EmitReady(config, *index, fds, initialL);

        for (const auto& p : config.cases) {
            ApplyCase(*index, p);
            std::array<std::optional<RunResult>, 2> first;
            for (int repeat = 0; repeat < config.repeats; ++repeat) {
                for (int order = 0; order < 2; ++order) {
                    const bool profile = (repeat + order) % 2 != 0;
                    auto result = Run(*index, *queries, truth, config, p, repeat, profile);
                    VerifyCase(*index, p);
                    const int same = profile ? 1 : 0, other = 1 - same;
                    if (first[same])
                        Compare(result, *first[same], config, PointContext(p, repeat, profile), Mode(profile), profile);
                    if (first[other])
                        Compare(result, *first[other], config, PointContext(p, repeat, profile), Mode(!profile), false);
                    EmitPoint(result, config, p, repeat, profile, first[same].has_value(), first[other].has_value());
                    if (!first[same]) first[same] = std::move(result);
                }
            }
            std::cout << "VANILLA_EQUIVALENT {\"nprobe\":" << p.nprobe << ",\"runs\":" << config.repeats * 2
                      << ",\"exact_ids\":true,\"exact_float_bits\":true,\"get_scanned_equal\":true,"
                      << "\"profile_work_equal\":true}\n" << std::flush;
        }
        std::cout << "VANILLA_DONE {\"points\":" << config.cases.size() * config.repeats * 2
                  << ",\"all_comparisons_passed\":true}\n" << std::flush;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "VANILLA_ERROR {\"message\":" << Json(e.what()) << "}\n" << std::flush;
        return 1;
    }
}

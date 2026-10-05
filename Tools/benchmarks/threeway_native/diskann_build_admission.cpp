#include "diskann_loader_policy.h"
#include "linux_aligned_file_reader.h"

#include <boost/property_tree/json_parser.hpp>
#include <openssl/evp.h>

namespace
{
namespace fs = std::filesystem;
using Tree = boost::property_tree::ptree;
using threeway::require;
using threeway::json_string;
using threeway_diskann::AdmissionFile;

std::string sha256(const fs::path &path, uint64_t limit = UINT64_MAX)
{
    std::ifstream input(path, std::ios::binary);
    require(input.is_open(), "Cannot hash input: " + path.string());
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    require(context && EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) == 1, "Cannot initialize SHA256");
    std::array<char, 65536> buffer{};
    while (input && limit)
    {
        input.read(buffer.data(), static_cast<std::streamsize>(std::min<uint64_t>(buffer.size(), limit)));
        const auto count = static_cast<uint64_t>(input.gcount());
        require(EVP_DigestUpdate(context.get(), buffer.data(), count) == 1, "Cannot update SHA256");
        limit -= count;
    }
    require(!input.bad(), "Cannot read hashed input: " + path.string());
    unsigned char digest[EVP_MAX_MD_SIZE]{};
    unsigned int length = 0;
    require(EVP_DigestFinal_ex(context.get(), digest, &length) == 1 && length == 32, "Cannot finish SHA256");
    std::ostringstream out;
    for (unsigned int i = 0; i < length; ++i)
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(digest[i]);
    return out.str();
}

void unique_json_keys(const Tree &tree)
{
    std::set<std::string> keys;
    for (const auto &item : tree)
    {
        if (!item.first.empty())
            require(keys.insert(item.first).second, "Duplicate JSON metadata key: " + item.first);
        unique_json_keys(item.second);
    }
}

Tree read_json(const fs::path &path)
{
    Tree result;
    boost::property_tree::read_json(path.string(), result);
    unique_json_keys(result);
    return result;
}

uint64_t number(const Tree &tree, const std::string &key)
{
    return threeway::parse_uint(tree.get<std::string>(key), "JSON " + key);
}

struct InputIdentity
{
    AdmissionFile file;
    std::string first_4096_sha256, full_sha256;

    static InputIdentity capture(const fs::path &path, bool full = false)
    {
        InputIdentity result{AdmissionFile::capture(path), sha256(path, 4096), full ? sha256(path) : ""};
        result.file.verify();
        return result;
    }

    std::string json() const
    {
        auto result = file.json();
        result.pop_back();
        return result + ",\"first_4096_sha256\":" + json_string(first_4096_sha256) +
               ",\"sha256\":" + (full_sha256.empty() ? "null" : json_string(full_sha256)) + "}";
    }
};

class Certificate
{
    int fd_ = -1;
    bool written_ = false;

  public:
    explicit Certificate(const fs::path &path)
    {
        for (auto parent = path.parent_path(); !parent.empty(); parent = parent.parent_path())
        {
            require(!fs::is_symlink(parent) && fs::is_directory(parent),
                    "Certificate ancestry must be existing real directories: " + parent.string());
            if (parent == parent.root_path())
                break;
        }
        fd_ = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0444);
        require(fd_ >= 0, "Certificate must be a fresh, exclusively created file: " + path.string());
    }
    ~Certificate()
    {
        if (fd_ >= 0)
            ::close(fd_);
    }
    void write(const std::string &record)
    {
        require(!written_, "Certificate cannot be rewritten");
        const std::string payload = record + "\n";
        size_t position = 0;
        while (position < payload.size())
        {
            const ssize_t count = ::write(fd_, payload.data() + position, payload.size() - position);
            if (count < 0 && errno == EINTR)
                continue;
            require(count > 0, "Cannot persist admission certificate");
            position += static_cast<size_t>(count);
        }
        require(::fsync(fd_) == 0 && ::fchmod(fd_, 0444) == 0, "Cannot seal admission certificate");
        written_ = true;
    }
};

class BuildAdmission
{
    const fs::path config_;
    const fs::path loader_policy_path_;
    threeway_diskann::LoaderPolicy loader_policy_;
    const threeway::Clock::time_point started_ = threeway::Clock::now();
    std::vector<InputIdentity> identities_;
    std::string guard_json_ = "null";
    fs::path root_, prefix_, search_binary_;
    uint64_t rows_ = 0, planned_queries_ = 0, aio_used_ = 0, aio_maximum_ = 0, aio_needed_ = 0;
    uint32_t dimension_ = 0, columns_ = 0, column_ = 0, k_ = 0, l_ = 0, threads_ = 0, queries_per_label_ = 0;
    std::vector<uint32_t> configured_, selected_;
    std::map<uint32_t, uint64_t> counts_, native_counts_;
    std::map<uint32_t, uint32_t> converted_;
    std::vector<threeway::Scenario> scenarios_;
    std::vector<threeway::Job> jobs_;
    bool converted_verified_ = false, native_loaded_ = false;

    static fs::path path(const threeway::Ini &ini, const std::string &section, const std::string &key)
    {
        const fs::path result(ini.require(section, key));
        require(result.is_absolute(), "Original build admission requires explicit absolute INI paths: " + key);
        return result.lexically_normal();
    }

    const InputIdentity &remember(const fs::path &file, bool full = false)
    {
        for (auto &entry : identities_)
            if (entry.file.path == file)
            {
                if (full && entry.full_sha256.empty())
                {
                    entry.full_sha256 = sha256(file);
                    entry.file.verify();
                }
                return entry;
            }
        identities_.push_back(InputIdentity::capture(file, full));
        return identities_.back();
    }

    void verify_identities() const
    {
        for (const auto &entry : identities_)
            entry.file.verify();
        loader_policy_.verify();
    }

    void original_identity(const Tree &expected, const fs::path &file)
    {
        const auto &actual = remember(file);
        require(expected.get<std::string>("path") == actual.file.resolved.string() &&
                    number(expected, "bytes") == actual.file.bytes &&
                    number(expected, "device") == actual.file.device &&
                    number(expected, "inode") == actual.file.inode &&
                    number(expected, "mtime_ns") == static_cast<uint64_t>(actual.file.mtime_ns) &&
                    expected.get<std::string>("first_4096_sha256") == actual.first_4096_sha256,
                "Original build input/binary identity changed: " + file.string());
    }

    void check_aio()
    {
        aio_used_ = threeway_diskann::kernel_counter("/proc/sys/fs/aio-nr");
        aio_maximum_ = threeway_diskann::kernel_counter("/proc/sys/fs/aio-max-nr");
        aio_needed_ = threeway::checked_product(threads_, 1024, "Original validation AIO reservation");
        require(aio_used_ <= aio_maximum_ && aio_needed_ <= aio_maximum_ - aio_used_,
                "Insufficient current AIO headroom for original ValidationThreads; no limits or threads changed");
    }

    void load_counts(const fs::path &file)
    {
        std::ifstream input(file);
        std::string line;
        require(static_cast<bool>(std::getline(input, line)), "Empty original categorical count table");
        const auto header = threeway::split(line, '\t');
        const auto attribute = std::find(header.begin(), header.end(), "attribute_id");
        const auto count = std::find(header.begin(), header.end(), "count");
        require(attribute != header.end() && count != header.end() &&
                    std::set<std::string>(header.begin(), header.end()).size() == header.size(),
                "Invalid original count-table columns");
        uint64_t total = 0;
        while (std::getline(input, line))
        {
            const auto fields = threeway::split(line, '\t');
            require(fields.size() == header.size(), "Malformed categorical count-table row");
            const uint32_t label = threeway::u32(threeway::parse_uint(
                fields[static_cast<size_t>(attribute - header.begin())], "attribute_id"), "attribute_id", true);
            const uint64_t value = threeway::parse_uint(fields[static_cast<size_t>(count - header.begin())], "count");
            require(value <= rows_ - total && counts_.emplace(label, value).second,
                    "Duplicate label or overflowing categorical counts");
            total += value;
        }
        require(input.eof() && !counts_.empty() && total == rows_, "Categorical counts do not sum to native rows");
    }

    void derive(const threeway::Ini &ini)
    {
        ini.only("Inputs", {"vectors", "queries", "attributes", "attributecolumns", "categoricalcolumn",
                            "counts", "attributemanifest", "preparedlabels", "preparedlabelsmanifest", "pqprefix"});
        ini.only("DiskANN", {"sourcedirectory", "binarydirectory", "pqcheckbinary", "sourcerevision", "r", "l",
                             "filteredl", "alpha", "threads", "labeltype", "pqchunks", "pqchecksamples"});
        ini.only("Run", {"outputdirectory", "smokeoutputdirectory", "scratchparent", "minimumfreediskgib",
                         "minimumfreememorygib", "maxbuilderrssgib", "chunkrows", "smokevectors",
                         "validationqueriesperlabel", "validationk", "validationl", "validationthreads",
                         "validationlabels", "preflightdiagnostics", "acceptnativerecallrisk"});
        root_ = path(ini, "Run", "OutputDirectory");
        prefix_ = root_ / "sift1b";
        require(fs::equivalent(config_, root_ / "config.ini"),
                "--config must name the original frozen Run.OutputDirectory/config.ini");
        require(ini.require("DiskANN", "SourceRevision") == THREEWAY_DISKANN_REVISION &&
                    fs::equivalent(path(ini, "DiskANN", "SourceDirectory"), THREEWAY_DISKANN_SOURCE) &&
                    ini.require("DiskANN", "LabelType") == "uint",
                "Build INI differs from the pinned original UInt8/L2/uint-label library");
        k_ = threeway::u32(ini.uint_value("Run", "ValidationK"), "Run.ValidationK");
        l_ = threeway::u32(ini.uint_value("Run", "ValidationL"), "Run.ValidationL");
        threads_ = threeway::u32(ini.uint_value("Run", "ValidationThreads"), "Run.ValidationThreads");
        queries_per_label_ = threeway::u32(ini.uint_value("Run", "ValidationQueriesPerLabel"),
                                         "Run.ValidationQueriesPerLabel");
        require(l_ >= k_ && threads_ <= static_cast<uint32_t>(INT32_MAX),
                "Original validation requires L>=K and a valid positive OpenMP thread count");
        configured_ = threeway::uint_list(ini, "Run", "ValidationLabels", true);
        columns_ = threeway::u32(ini.uint_value("Inputs", "AttributeColumns"), "Inputs.AttributeColumns");
        column_ = threeway::u32(ini.uint_value("Inputs", "CategoricalColumn"), "Inputs.CategoricalColumn", true);
        require(column_ < columns_, "Invalid original categorical column");

        const fs::path manifest_path = root_ / "manifest.json";
        remember(manifest_path, true);
        const Tree manifest = read_json(manifest_path);
        require(manifest.get<std::string>("native_source_revision") == THREEWAY_DISKANN_REVISION &&
                    manifest.get<std::string>("config_sha256") == sha256(config_) &&
                    manifest.get<std::string>("graph_reused") == "false" &&
                    manifest.get<std::string>("search_pq_reused") == "true" &&
                    manifest.get<std::string>("categorical_only") == "true" &&
                    manifest.get<std::string>("disk_pq") == "false" &&
                    manifest.get<std::string>("universal_label") == "null" &&
                    manifest.get<std::string>("value_type") == "uint8" &&
                    manifest.get<std::string>("metric") == "l2",
                "Original build manifest/configuration/proof restrictions do not agree");
        const fs::path runner = root_ / "build_categorical_from_pq.py";
        require(remember(runner, true).full_sha256 == manifest.get<std::string>("runner_sha256"),
                "Original controller source identity changed");
        for (const std::string key : {"R", "L", "FilteredL", "Alpha", "Threads"})
            require(manifest.get<std::string>("graph_parameters." + key) == ini.require("DiskANN", key),
                    "Original manifest graph parameter differs from frozen INI: " + key);

        const auto vectors = path(ini, "Inputs", "Vectors");
        const auto queries = path(ini, "Inputs", "Queries");
        const auto attributes = path(ini, "Inputs", "Attributes");
        const auto counts = path(ini, "Inputs", "Counts");
        const auto attributes_manifest = path(ini, "Inputs", "AttributeManifest");
        const auto pq_prefix = path(ini, "Inputs", "PQPrefix").string();
        std::set<fs::path> required_inputs{vectors, queries, attributes, counts, attributes_manifest,
                                         pq_prefix + "_pq_pivots.bin", pq_prefix + "_pq_compressed.bin",
                                         path(ini, "Inputs", "PreparedLabels"),
                                         path(ini, "Inputs", "PreparedLabelsManifest"),
                                         path(ini, "Run", "PreflightDiagnostics")};
        for (const auto &truth : ini.section("Truth"))
            required_inputs.insert(path(ini, "Truth", truth.first));
        const auto &original_inputs = manifest.get_child("inputs");
        require(original_inputs.size() == required_inputs.size(), "Original build input inventory differs from INI");
        for (const auto &file : required_inputs)
        {
            const auto found = original_inputs.find(file.string());
            require(found != original_inputs.not_found(), "Missing original input identity: " + file.string());
            original_identity(found->second, file);
        }

        const auto base_shape = threeway::matrix_shape<uint8_t>(vectors);
        const auto query_shape = threeway::matrix_shape<uint8_t>(queries);
        const auto pq_shape = threeway::matrix_shape<uint8_t>(pq_prefix + "_pq_compressed.bin");
        rows_ = base_shape.rows;
        dimension_ = base_shape.cols;
        require(rows_ < UINT32_MAX && k_ <= rows_ && number(manifest, "vectors") == rows_ &&
                    number(manifest, "dimension") == dimension_ && query_shape.cols == dimension_ &&
                    query_shape.rows >= queries_per_label_ && pq_shape.rows == rows_ &&
                    pq_shape.cols == ini.uint_value("DiskANN", "PQChunks"),
                "Original vector/query/PQ schema differs from the frozen build configuration");
        require(fs::file_size(attributes) == threeway::checked_product(
                    threeway::checked_product(rows_, columns_, "Attribute schema"), 4, "Attribute schema"),
                "Original attributes must be exact headerless row-major uint32");
        const auto metadata = read_json(attributes_manifest);
        require(number(metadata, "vector_count") == rows_ && number(metadata, "dimension") == dimension_ &&
                    number(metadata, "attribute_columns") == columns_ &&
                    number(metadata, "limited_tag_column") == column_,
                "Original attribute manifest schema mismatch");
        require(remember(counts, true).full_sha256 == metadata.get<std::string>("files.counts.sha256"),
                "Original categorical count table failed its manifest SHA256");
        remember(attributes_manifest, true);
        load_counts(counts);

        for (uint32_t label : configured_)
            require(counts_.count(label) && counts_.at(label) >= k_,
                    "Configured production validation label has fewer than K source vectors: " + std::to_string(label));
        selected_ = configured_;
        for (const auto &entry : counts_)
            if (entry.second >= k_ && std::find(configured_.begin(), configured_.end(), entry.first) == configured_.end())
                selected_.push_back(entry.first);
        require(!selected_.empty(), "No original validation labels were selected");
        for (size_t i = 0; i < selected_.size(); ++i)
        {
            const uint32_t label = selected_[i];
            threeway::Scenario scenario;
            scenario.name = "label_" + std::to_string(label);
            scenario.kind = threeway::ScenarioKind::categorical;
            scenario.tag = label;
            scenario.candidate_count = counts_.at(label);
            scenarios_.push_back(scenario);
            jobs_.push_back({i, 0, 0, l_, threads_, 1});
            planned_queries_ += i < configured_.size() ? queries_per_label_ : 1;
        }
        search_binary_ = path(ini, "DiskANN", "BinaryDirectory") / "search_disk_index";
        for (const std::string name : {"build_memory_index", "create_disk_layout", "search_disk_index"})
        {
            const auto binary = path(ini, "DiskANN", "BinaryDirectory") / name;
            original_identity(manifest.get_child("binaries." + name + ".identity"), binary);
            require(remember(binary, true).full_sha256 == manifest.get<std::string>("binaries." + name + ".sha256"),
                    "Original native executable changed: " + name);
        }
        require(fs::equivalent(prefix_.string() + "_pq_compressed.bin", pq_prefix + "_pq_compressed.bin"),
                "Original reused PQ-code identity differs from native prefix");
        require(sha256(prefix_.string() + "_pq_pivots.bin") == sha256(pq_prefix + "_pq_pivots.bin"),
                "Native PQ pivots differ from the original reused pivots");
        if (const auto saved = manifest.get_optional<std::string>("index_prefix"))
            require(*saved == prefix_.string(), "Original manifest index prefix differs from Run.OutputDirectory/sift1b");
        verify_identities();
    }

  public:
    BuildAdmission(fs::path config, fs::path loader_policy)
        : config_(std::move(config)), loader_policy_path_(std::move(loader_policy))
    {
    }

    void execute()
    {
        threeway::reject_environment_overrides();
        threeway::check_native_host();
        remember(config_, true);
        const threeway::Ini ini(config_);
        derive(ini);
        loader_policy_.authenticate(loader_policy_path_);
        if (loader_policy_.active())
            search_binary_ = loader_policy_.stock_binary();
        const threeway_diskann::AdmissionParameters parameters{"build-validation", rows_, dimension_, k_, 0,
                                                              scenarios_, jobs_};
        threeway_diskann::AdmissionGuard guard(parameters, [&](const std::string &record) { guard_json_ = record; });
        try
        {
            guard.prepare(prefix_.string(), 2);
            guard.prove_reachability();
            native_counts_ = guard.native_label_counts();
            converted_ = guard.external_labels;
            std::set<uint32_t> distinct_native_labels;
            for (const auto &entry : converted_)
            {
                require(distinct_native_labels.insert(entry.second).second,
                        "Original one-label-per-vector build has aliased converted labels");
                require(native_counts_.at(entry.second) == counts_.at(entry.first),
                        "Native label membership count differs from original authenticated source count: " +
                        std::to_string(entry.first));
            }
            guard.release_temporary_membership();
            check_aio();
            omp_set_dynamic(0);
            omp_set_max_active_levels(1);
            omp_set_num_threads(static_cast<int>(threads_));
            std::shared_ptr<AlignedFileReader> reader = std::make_shared<LinuxAlignedFileReader>();
            diskann::PQFlashIndex<uint8_t, uint32_t> index(reader, diskann::Metric::L2);
            const auto started = threeway::Clock::now();
            require(index.load(threads_, prefix_.c_str()) == 0, "Original native index load failed");
            native_loaded_ = true;
            guard.native_loaded(std::chrono::duration<double>(threeway::Clock::now() - started).count());
            require(index.get_num_points() == rows_ && index.get_data_dim() == dimension_ &&
                        index.get_max_degree() <= ini.uint_value("DiskANN", "R"),
                    "Loaded native shape/degree differs from original build INI");
            for (const auto &entry : converted_)
                require(index.get_converted_label(std::to_string(entry.first)) == entry.second,
                        "Original get_converted_label disagrees with admission metadata");
            converted_verified_ = true;
            guard.verify_files();
            verify_identities();
            guard.finish();
        }
        catch (const std::exception &error)
        {
            native_counts_ = guard.native_label_counts();
            converted_ = guard.external_labels;
            guard.finish(error.what());
            throw;
        }
    }

    std::string certificate(const std::string &error) const
    {
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::setprecision(17)
            << "{\"schema_version\":1,\"engine\":\"Filtered_DiskANN\""
               ",\"purpose\":\"original-all-label-build-validation-admission\",\"status\":"
            << json_string(error.empty() ? "admitted" : "rejected") << ",\"error\":" << json_string(error)
            << ",\"pid\":" << ::getpid() << ",\"native_search_invocations\":0,\"warmup_queries\":0,\"measured_queries\":0"
            << ",\"config\":" << json_string(config_.string()) << ",\"source_revision\":"
            << json_string(THREEWAY_DISKANN_REVISION) << ",\"linked_library\":" << json_string(THREEWAY_DISKANN_LIBRARY)
            << ",\"index_prefix\":" << json_string(prefix_.string())
            << ",\"stock_search_binary\":" << json_string(search_binary_.string())
            << ",\"vector_count\":" << rows_ << ",\"dimension\":" << dimension_
            << ",\"attribute_columns\":" << columns_ << ",\"categorical_column\":" << column_
            << ",\"validation_k\":" << k_ << ",\"validation_l\":" << l_ << ",\"validation_threads\":" << threads_
            << ",\"beam_width\":2,\"cache_nodes\":0,\"native_io_limit\":4294967295"
               ",\"stock_warmup_enabled\":false,\"automatic_beam_tuning\":false,\"reorder\":false"
            << ",\"validation_queries_per_configured_label\":" << queries_per_label_
            << ",\"planned_stock_query_calls\":" << planned_queries_
            << ",\"selection_policy\":\"configured labels in INI order, then other count>=K labels in numeric order\""
            << ",\"native_index_loaded\":" << (native_loaded_ ? "true" : "false")
            << ",\"converted_labels_verified\":" << (converted_verified_ ? "true" : "false")
            << ",\"aio\":{\"used\":" << aio_used_ << ",\"maximum\":" << aio_maximum_
            << ",\"required\":" << aio_needed_ << ",\"events_per_thread\":1024}"
            << ",\"elapsed_seconds\":" << std::chrono::duration<double>(threeway::Clock::now() - started_).count()
            << ",\"configured_labels\":[";
        for (size_t i = 0; i < configured_.size(); ++i)
            out << (i ? "," : "") << configured_[i];
        out << "],\"selected_labels\":[";
        for (size_t i = 0; i < selected_.size(); ++i)
        {
            const auto label = selected_[i];
            const auto found = converted_.find(label);
            out << (i ? "," : "") << "{\"external_label\":" << label << ",\"source_count\":" << counts_.at(label)
                << ",\"configured\":" << (i < configured_.size() ? "true" : "false")
                << ",\"planned_queries\":" << (i < configured_.size() ? queries_per_label_ : 1)
                << ",\"scenario\":" << json_string("label_" + std::to_string(label)) << ",\"native_label\":";
            if (found == converted_.end())
                out << "null,\"native_count\":null";
            else
                out << found->second << ",\"native_count\":" << native_counts_.at(found->second);
            out << "}";
        }
        out << "],\"skipped_below_k\":[";
        bool comma = false;
        for (const auto &entry : counts_)
            if (entry.second < k_)
            {
                out << (comma ? "," : "") << "{\"external_label\":" << entry.first << ",\"source_count\":"
                    << entry.second << "}";
                comma = true;
            }
        out << "],\"input_identities\":[";
        for (size_t i = 0; i < identities_.size(); ++i)
            out << (i ? "," : "") << identities_[i].json();
        out << "],\"admission\":" << guard_json_;
        if (loader_policy_.active())
            out << ",\"loader_policy\":" << loader_policy_.json();
        out << "}";
        return out.str();
    }
};
} // namespace

int main(int argc, char **argv)
{
    try
    {
        require((argc == 5 || (argc == 7 && std::string(argv[5]) == "--loader-policy")) &&
                    std::string(argv[1]) == "--config" && std::string(argv[3]) == "--certificate",
                "Usage: diskannBuildAdmission --config ORIGINAL_BUILD.ini --certificate FRESH.json "
                "[--loader-policy ABSOLUTE_POLICY.ini]");
        const auto config = fs::absolute(argv[2]).lexically_normal();
        const auto output = fs::absolute(argv[4]).lexically_normal();
        Certificate certificate(output);
        BuildAdmission admission(config, argc == 7 ? fs::path(argv[6]) : fs::path{});
        std::string error;
        try
        {
            admission.execute();
        }
        catch (const std::exception &failure)
        {
            error = failure.what();
        }
        const std::string record = admission.certificate(error);
        certificate.write(record);
        std::cout << "DISKANN_BUILD_ADMISSION " << record << std::endl;
        return error.empty() ? 0 : 1;
    }
    catch (const std::exception &failure)
    {
        std::cerr << "DISKANN_BUILD_ADMISSION_ERROR " << failure.what() << '\n';
        return 1;
    }
}

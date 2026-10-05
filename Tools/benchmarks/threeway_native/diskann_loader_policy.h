#pragma once

#include "diskann_admission.h"

#include <boost/property_tree/json_parser.hpp>
#include <openssl/evp.h>

#ifndef THREEWAY_DISKANN_LOADER_POLICY_JSON
#define THREEWAY_DISKANN_LOADER_POLICY_JSON ""
#endif
#ifndef THREEWAY_DISKANN_ORIGINAL_LIBRARY
#define THREEWAY_DISKANN_ORIGINAL_LIBRARY THREEWAY_DISKANN_LIBRARY
#endif

namespace threeway_diskann
{
class LoaderPolicy
{
    using Tree = boost::property_tree::ptree;
    std::string json_;
    fs::path stock_binary_;
    std::vector<AdmissionFile> files_;

    static std::string hash(const fs::path &path)
    {
        std::ifstream input(path, std::ios::binary);
        threeway::require(input.is_open(), "Cannot hash loader metadata: " + path.string());
        std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
        threeway::require(context && EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) == 1,
                          "Cannot initialize loader SHA256");
        std::array<char, 65536> buffer{};
        while (input)
        {
            input.read(buffer.data(), buffer.size());
            threeway::require(EVP_DigestUpdate(context.get(), buffer.data(), input.gcount()) == 1,
                              "Cannot update loader SHA256");
        }
        threeway::require(input.eof() && !input.bad(), "Cannot read loader metadata: " + path.string());
        unsigned char digest[EVP_MAX_MD_SIZE]{};
        unsigned int size = 0;
        threeway::require(EVP_DigestFinal_ex(context.get(), digest, &size) == 1 && size == 32,
                          "Cannot finish loader SHA256");
        std::ostringstream out;
        for (unsigned int i = 0; i < size; ++i)
            out << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(digest[i]);
        return out.str();
    }

    AdmissionFile remember(const fs::path &path, const std::string &digest)
    {
        const auto file = AdmissionFile::capture(path);
        threeway::require(path.is_absolute() && file.path == file.resolved,
                          "Loader authority requires canonical absolute paths");
        threeway::require(hash(path) == digest, "Loader authority SHA256 mismatch: " + path.string());
        file.verify();
        files_.push_back(file);
        return file;
    }

    void identity(const Tree &expected)
    {
        const auto actual = remember(expected.get<std::string>("path"), expected.get<std::string>("sha256"));
        const auto number = [&](const char *key) {
            return threeway::parse_uint(expected.get<std::string>(key), std::string("Loader identity ") + key);
        };
        threeway::require(expected.size() == 8 &&
                              expected.get<std::string>("resolved") == actual.resolved.string() &&
                              number("bytes") == actual.bytes && number("device") == actual.device &&
                              number("inode") == actual.inode &&
                              number("mtime_ns") == static_cast<uint64_t>(actual.mtime_ns) &&
                              number("ctime_ns") == static_cast<uint64_t>(actual.ctime_ns),
                          "Loader authority file identity mismatch: " + actual.path.string());
    }

  public:
    void authenticate(const fs::path &path)
    {
        const std::string compiled = THREEWAY_DISKANN_LOADER_POLICY_JSON;
        if (compiled.empty())
        {
            threeway::require(path.empty(), "This native binary was built without an approved loader policy");
            return;
        }
        Tree expected;
        std::istringstream metadata(compiled);
        boost::property_tree::read_json(metadata, expected);
        threeway::require(expected.size() == 7 && path.is_absolute() &&
                              path.string() == expected.get<std::string>("path"),
                          "This linked library requires its exact absolute --loader-policy metadata");
        remember(path, expected.get<std::string>("sha256"));
        const threeway::Ini ini(path);
        ini.only("Loader", {"mode", "proof", "proofsha256", "stockbinary", "library"});
        threeway::require(ini.section("Loader").size() == 5 &&
                              ini.require("Loader", "Mode") == "linear-label-delimiters-v1" &&
                              ini.require("Loader", "Mode") == expected.get<std::string>("mode") &&
                              ini.require("Loader", "Proof") == expected.get<std::string>("proof") &&
                              ini.require("Loader", "ProofSHA256") == expected.get<std::string>("proof_sha256") &&
                              ini.require("Loader", "StockBinary") == expected.get<std::string>("stock_binary") &&
                              ini.require("Loader", "Library") == expected.get<std::string>("library") &&
                              ini.require("Loader", "Library") == THREEWAY_DISKANN_LIBRARY,
                          "Loader INI metadata differs from the linked native library binding");
        remember(ini.require("Loader", "Proof"), ini.require("Loader", "ProofSHA256"));
        Tree proof;
        boost::property_tree::read_json(ini.require("Loader", "Proof"), proof);
        threeway::require(proof.get<std::string>("schema_version") == "1" &&
                              proof.get<std::string>("purpose") == "diskann-loader-only-linear-label-delimiters-v1" &&
                              proof.get<std::string>("base_revision") == THREEWAY_DISKANN_REVISION &&
                              proof.get<std::string>("original_source_tree") == THREEWAY_DISKANN_SOURCE &&
                              proof.get<std::string>("original_library.path") == THREEWAY_DISKANN_ORIGINAL_LIBRARY,
                          "Loader proof must retain the original algorithm/source/archive authority");
        threeway::require(proof.get<std::string>("patched_library.path") == expected.get<std::string>("library") &&
                              proof.get<std::string>("patched_stock_binary.path") == expected.get<std::string>("stock_binary"),
                          "Loader proof artifact paths differ from the compiled binary binding");
        identity(proof.get_child("patched_library"));
        identity(proof.get_child("patched_stock_binary"));
        identity(proof.get_child("original_library"));
        identity(proof.get_child("original_stock_binary"));
        identity(proof.get_child("original_pq_flash_index_cpp"));
        identity(proof.get_child("patched_pq_flash_index_cpp"));
        verify();
        stock_binary_ = ini.require("Loader", "StockBinary");
        json_ = compiled;
    }

    void verify() const
    {
        for (const auto &file : files_)
            file.verify();
    }

    bool active() const { return !json_.empty(); }
    const std::string &json() const { return json_; }
    const fs::path &stock_binary() const { return stock_binary_; }
};
} // namespace threeway_diskann

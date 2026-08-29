// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_HELPER_ATOMICFILE_H_
#define _SPTAG_HELPER_ATOMICFILE_H_

#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <string>
#include <algorithm>
#include <vector>

#ifdef _WIN32
#include <io.h>
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace SPTAG
{
namespace Helper
{

inline bool SyncParentDirectory(
    const std::string& p_path)
{
#ifdef _WIN32
    (void)p_path;
    return true;
#else
    std::filesystem::path parent =
        std::filesystem::path(
            p_path).parent_path();
    if (parent.empty()) parent = ".";
    int directoryFlags = O_RDONLY;
#ifdef O_DIRECTORY
    directoryFlags |= O_DIRECTORY;
#endif
    const int directory = open(
        parent.c_str(), directoryFlags);
    if (directory < 0) return false;
    const bool synced = fsync(directory) == 0;
    const bool closed = close(directory) == 0;
    return synced && closed;
#endif
}

inline bool SyncFile(
    const std::filesystem::path& p_path)
{
#ifdef _WIN32
    const HANDLE file = CreateFileA(
        p_path.string().c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE |
            FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    const bool synced =
        FlushFileBuffers(file) != 0;
    const bool closed =
        CloseHandle(file) != 0;
    return synced && closed;
#else
    const int file = open(
        p_path.c_str(), O_RDONLY);
    if (file < 0) return false;
    const bool synced = fsync(file) == 0;
    const bool closed = close(file) == 0;
    return synced && closed;
#endif
}

inline bool SyncDirectoryTree(
    const std::string& p_root)
{
#ifdef _WIN32
    (void)p_root;
    return true;
#else
    const std::filesystem::path root(p_root);
    std::error_code error;
    if (!std::filesystem::is_directory(
            root, error) ||
        error) {
        return false;
    }
    std::vector<std::filesystem::path>
        directories = {root};
    for (std::filesystem::recursive_directory_iterator
             it(root, error),
         end;
         !error && it != end;
         it.increment(error)) {
        if (it->is_symlink(error)) {
            continue;
        } else if (it->is_directory(error)) {
            directories.push_back(it->path());
        } else if (!error &&
                   it->is_regular_file(error) &&
                   !SyncFile(it->path())) {
            return false;
        }
        if (error) return false;
    }
    if (error) return false;
    std::sort(
        directories.begin(), directories.end(),
        [](const std::filesystem::path& p_left,
           const std::filesystem::path& p_right) {
            return p_left.native().size() >
                p_right.native().size();
        });
    for (const auto& directoryPath :
         directories) {
        int directoryFlags = O_RDONLY;
#ifdef O_DIRECTORY
        directoryFlags |= O_DIRECTORY;
#endif
        const int directory = open(
            directoryPath.c_str(),
            directoryFlags);
        if (directory < 0) return false;
        const bool synced =
            fsync(directory) == 0;
        const bool closed =
            close(directory) == 0;
        if (!synced || !closed) return false;
    }
    return SyncParentDirectory(root.string());
#endif
}

inline bool GetOpenFileSize(
    FILE* p_file,
    std::uint64_t& p_bytes)
{
    p_bytes = 0;
    if (p_file == nullptr) return false;
#ifdef _WIN32
    const intptr_t osHandle =
        _get_osfhandle(_fileno(p_file));
    if (osHandle == -1) return false;
    LARGE_INTEGER fileSize;
    if (GetFileSizeEx(
            reinterpret_cast<HANDLE>(osHandle),
            &fileSize) == 0 ||
        fileSize.QuadPart < 0) {
        return false;
    }
    p_bytes =
        static_cast<std::uint64_t>(
            fileSize.QuadPart);
#else
    struct stat fileStatus;
    if (fstat(fileno(p_file), &fileStatus) != 0 ||
        fileStatus.st_size < 0) {
        return false;
    }
    p_bytes =
        static_cast<std::uint64_t>(
            fileStatus.st_size);
#endif
    return true;
}

inline bool AtomicReplaceFile(
    const std::string& p_temporary,
    const std::string& p_destination)
{
    std::filesystem::path parent =
        std::filesystem::path(
            p_destination).parent_path();
    if (parent.empty()) parent = ".";
#ifdef _WIN32
    const DWORD parentAttributes =
        GetFileAttributesA(parent.string().c_str());
    if (parentAttributes == INVALID_FILE_ATTRIBUTES ||
        (parentAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0) {
        return false;
    }
    const HANDLE temporaryFile = CreateFileA(
        p_temporary.c_str(),
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE |
            FILE_SHARE_DELETE,
        nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (temporaryFile == INVALID_HANDLE_VALUE) {
        return false;
    }
    const bool fileSynced =
        FlushFileBuffers(temporaryFile) != 0;
    const bool fileClosed =
        CloseHandle(temporaryFile) != 0;
    if (!fileSynced || !fileClosed) return false;
    return MoveFileExA(
               p_temporary.c_str(),
               p_destination.c_str(),
               MOVEFILE_REPLACE_EXISTING |
                   MOVEFILE_WRITE_THROUGH) != 0;
#else
    int directoryFlags = O_RDONLY;
#ifdef O_DIRECTORY
    directoryFlags |= O_DIRECTORY;
#endif
    const int directory = open(
        parent.c_str(), directoryFlags);
    if (directory < 0) return false;

    const int temporaryFile = open(
        p_temporary.c_str(), O_RDONLY);
    if (temporaryFile < 0) {
        close(directory);
        return false;
    }
    const bool fileSynced =
        fsync(temporaryFile) == 0;
    const bool fileClosed =
        close(temporaryFile) == 0;
    if (!fileSynced || !fileClosed) {
        close(directory);
        return false;
    }
    if (std::rename(
            p_temporary.c_str(),
            p_destination.c_str()) != 0) {
        close(directory);
        return false;
    }
    const bool directorySynced =
        fsync(directory) == 0;
    const bool directoryClosed =
        close(directory) == 0;
    return directorySynced &&
        directoryClosed;
#endif
}

} // namespace Helper
} // namespace SPTAG

#endif

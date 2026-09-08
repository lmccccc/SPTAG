// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_COMMON_VISITEDBITMAP_H_
#define _SPTAG_COMMON_VISITEDBITMAP_H_

#include <cstddef>
#include <cstdint>
#include <vector>

namespace SPTAG
{
namespace COMMON
{

class VisitedBitmap
{
public:
    void ResetSeen(size_t count)
    {
        for (size_t word : m_dirtyWords) m_words[word] = 0;
        m_dirtyWords.clear();
        const size_t words = count / 64 + (count % 64 != 0);
        if (m_words.size() < words) m_words.resize(words, 0);
        m_count = count;
    }

    bool CheckAndSet(size_t id)
    {
        const size_t word = id >> 6;
        const std::uint64_t mask = std::uint64_t{1} << (id & 63);
        if ((m_words[word] & mask) != 0) return true;
        if (m_words[word] == 0) m_dirtyWords.push_back(word);
        m_words[word] |= mask;
        return false;
    }

    bool Contains(size_t id) const
    {
        return (m_words[id >> 6] & (std::uint64_t{1} << (id & 63))) != 0;
    }

    size_t Size() const { return m_count; }

private:
    std::vector<std::uint64_t> m_words;
    std::vector<size_t> m_dirtyWords;
    size_t m_count = 0;
};

} // namespace COMMON
} // namespace SPTAG

#endif

// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once

// Included inside SPTAG::COMMON after OptHashPosVector.
class NavigationVisited : public OptHashPosVector
{
    static constexpr std::uint32_t flag = 0x80000000U, mask = flag - 1;
    std::uint32_t* Find(std::uint32_t* table, int size, std::uint32_t key) const
    {
        unsigned at = hash_func(key, size);
        for (int loop = 0; loop < m_maxLoop; ++loop) {
            if (!table[at] || (table[at] & mask) == key) return table + at;
            at = hash_func2(at, size, loop);
        }
        return nullptr;
    }
    void GrowMatch()
    {
        int size = m_poolSize;
        for (;;) {
            if (size > (std::numeric_limits<int>::max() / 4) - 1)
                throw std::overflow_error("Navigation visited capacity overflow");
            size = (size + 1) * 2 - 1;
            std::unique_ptr<SizeType[]> next(new SizeType[std::size_t(size + 1) * 2]());
            auto* dest = reinterpret_cast<std::uint32_t*>(next.get());
            const auto* old = reinterpret_cast<const std::uint32_t*>(m_hashTable.get());
            bool second = false, complete = true;
            for (std::size_t i = 0; i < std::size_t(m_poolSize + 1) * 2; ++i) if (old[i]) {
                auto* slot = Find(dest, size, old[i] & mask);
                if (!slot) { slot = Find(dest + size + 1, size, old[i] & mask); second = true; }
                if (!slot) { complete = false; break; }
                *slot = old[i];
            }
            if (!complete) continue;
            while (m_poolSize < size) { m_poolSize = (m_poolSize + 1) * 2 - 1; ++m_exp; }
            m_hashTable = std::move(next);
            m_secondHash = second;
            return;
        }
    }
public:
    bool ContainsMatch(SizeType id) const
    {
        if (id < 0 || id == MaxSize) throw std::out_of_range("Invalid physical navigation ID");
        if (!m_hashTable) return false;
        const auto key = static_cast<std::uint32_t>(id) + 1U;
        auto* data = reinterpret_cast<std::uint32_t*>(m_hashTable.get());
        auto* slot = Find(data, m_poolSize, key);
        if (slot) return *slot != 0;
        slot = Find(data + m_poolSize + 1, m_poolSize, key);
        return slot && *slot != 0;
    }
    template<class Predicate>
    std::pair<bool, bool> Match(SizeType id, const Predicate& predicate)
    {
        static_assert(sizeof(SizeType) == 4 && std::is_signed<SizeType>::value, "Native 32-bit IDs");
        if (id < 0 || id == MaxSize) throw std::out_of_range("Invalid physical navigation ID");
        const auto key = static_cast<std::uint32_t>(id) + 1U;
        for (;;) {
            auto* data = reinterpret_cast<std::uint32_t*>(m_hashTable.get());
            auto* slot = Find(data, m_poolSize, key);
            bool second = false;
            if (!slot) { slot = Find(data + m_poolSize + 1, m_poolSize, key); second = true; }
            if (!slot) { GrowMatch(); continue; }
            if (*slot) return {true, (*slot & flag) != 0};
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (g_graphAccessStats) ++g_graphAccessStats->m_predicateCalls;
#endif
            const bool match = predicate(id);
            *slot = key | (match ? flag : 0U);
            m_secondHash |= second;
            return {false, match};
        }
    }
    const void* Storage() const { return m_hashTable.get(); }
    std::size_t StorageBytes() const { return sizeof(SizeType) * std::size_t(m_poolSize + 1) * 2; }
};

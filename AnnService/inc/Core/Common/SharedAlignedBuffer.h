// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>

namespace SPTAG { namespace COMMON {

class SharedAlignedBuffer {
public:
    static constexpr std::size_t Alignment = 64;

    explicit SharedAlignedBuffer(std::size_t bytes) : m_size(bytes)
    {
        if (bytes == 0 || bytes > (std::numeric_limits<std::size_t>::max)() - (Alignment - 1))
            throw std::invalid_argument("Invalid aligned storage size");
        std::size_t available = bytes + Alignment - 1;
        m_allocation.reset(new std::uint8_t[available]);
        void* address = m_allocation.get();
        if (std::align(Alignment, bytes, address, available) == nullptr)
            throw std::logic_error("Cannot align node storage");
        m_data = static_cast<std::uint8_t*>(address);
    }

    std::uint8_t* Data() { return m_data; }
    const std::uint8_t* Data() const { return m_data; }
    std::size_t Size() const { return m_size; }

private:
    std::unique_ptr<std::uint8_t[]> m_allocation;
    std::uint8_t* m_data = nullptr;
    std::size_t m_size;
};

}}

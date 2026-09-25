#pragma once
#include <cstdint>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace H1Supplier {
struct IntrinsicValidity {
    using Key = std::tuple<const void*, std::uint64_t, std::uint64_t,
                           std::uint64_t, std::uint64_t, int>;
    Key key{};
    bool ready = false;
    std::vector<unsigned char> flags;
    std::uint64_t rebuilds = 0;

    template<class Fill>
    const unsigned char* Ensure(const Key& next, Fill fill) {
        if (ready && key == next) return flags.data();
        const int count = std::get<5>(next);
        if (count < 0) throw std::runtime_error("Invalid intrinsic validity size");
        ready = false;
        flags.resize(static_cast<std::size_t>(count));
        for (int id = 0; id < count; ++id) {
            const int bits = fill(id);
            if (bits < 0 || bits > 3) throw std::runtime_error("Invalid intrinsic validity bits");
            flags[id] = static_cast<unsigned char>(bits);
        }
        key = next; ready = true; ++rebuilds;
        return flags.data();
    }
};
}

#include "NativeBatch.h"
#include <iostream>

int main() {
    const auto check = [](bool ok) { if (!ok) throw std::runtime_error("Batch parser fixture failed"); };
    check(NativeBatch::Probes("24", 10) == std::vector<int>({24}));
    check(NativeBatch::Probes("[16, 24,384]", 10) == std::vector<int>({16, 24, 384}));
    check(NativeBatch::Probes("384,24,16", 10) == std::vector<int>({384, 24, 16}));
    check(NativeBatch::Probes(" [ 24 ] ", 10) == std::vector<int>({24}));
    for (const auto* value : {"", "[]", "[ ]", "0", "-16", "+16", "1", "9",
                             "16,", ",16", "[16,,24]", "[16,24,]", "[16,16]",
                             "[016,16]", "[16", "16]", "[[16]]", "16.0", "1e2",
                             "2147483648", "18446744073709551616"}) {
        bool rejected = false;
        try { NativeBatch::Probes(value, 10); }
        catch (const std::runtime_error&) { rejected = true; }
        check(rejected);
    }
    std::cout << "PASS: scalar and ordered arrays; empty/malformed/nonpositive/overflow/"
                 "duplicate/below-ResultNum arrays rejected\n";
}

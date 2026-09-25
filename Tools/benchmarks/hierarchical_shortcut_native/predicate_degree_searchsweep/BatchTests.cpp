#include "../../NativeNProbeSweep.h"
#include <iostream>

int main() {
    const auto check = [](bool ok) { if (!ok) throw std::runtime_error("Shared parser fixture failed"); };
    check(NativeNProbeSweep::Parse("24", 10) == std::vector<int>({24}));
    check(NativeNProbeSweep::Parse("[16, 24,384]", 10) == std::vector<int>({16, 24, 384}));
    check(NativeNProbeSweep::Parse("384,24,16", 10) == std::vector<int>({384, 24, 16}));
    for (const auto* value : {"", "[]", "[ ]", "0", "-16", "+16", "9", "16,", ",16",
                             "[16,,24]", "[16,24,]", "[16,16]", "[016,16]", "[16", "16]",
                             "[[16]]", "16.0", "1e2", "2147483648", "18446744073709551616"}) {
        bool rejected = false;
        try { NativeNProbeSweep::Parse(value, 10); }
        catch (const std::invalid_argument&) { rejected = true; }
        check(rejected);
    }
    std::cout << "PASS: shared MAIN NativeNProbeSweep parser ordered/scalar/invalid fixtures\n";
}

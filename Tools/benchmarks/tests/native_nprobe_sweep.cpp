#include "../NativeNProbeSweep.h"

#include <cassert>
#include <climits>

int main()
{
    using NativeNProbeSweep::Parse;
    assert((Parse("[16, 24, 384]", 10) == std::vector<int>{16, 24, 384}));
    assert((Parse("384,24,16", 10) == std::vector<int>{384, 24, 16}));
    assert((Parse(" 24 ", 10) == std::vector<int>{24}));
    assert((Parse(std::to_string(INT_MAX), 10) == std::vector<int>{INT_MAX}));
    for (const auto* text : {
             "", " ", "[]", "[ ]", "0", "-1", "9", "+16", "16.0", "1e2",
             "16,", ",16", "16,,24", "16,16", "[16,24", "16,24]",
             "2147483648", "16,word", "[[16]]", "16;24"}) {
        bool rejected = false;
        try {
            Parse(text, 10);
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        assert(rejected);
    }
}

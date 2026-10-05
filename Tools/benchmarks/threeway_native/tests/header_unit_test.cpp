#include "../benchmark.h"

template <typename Function> void rejects(Function function)
{
    bool rejected = false;
    try
    {
        function();
    }
    catch (const std::exception &)
    {
        rejected = true;
    }
    threeway::require(rejected, "Malformed scalar was accepted");
}

int main()
{
    try
    {
        using namespace threeway;
        require(parse_uint("18446744073709551615", "test") == UINT64_MAX, "uint64 boundary");
        for (const std::string value : {"", "+1", "-1", "1.0", "1x", "18446744073709551616"})
            rejects([&] { parse_uint(value, "test"); });
        for (const std::string value : {"nan", "inf", "0x1p2", "1e", ".", "1.2.3", "1e9999"})
            rejects([&] { parse_double(value, "test"); });
        require(parse_double("5e-2", "test") == 0.05 && parse_double("+3.25", "test") == 3.25,
                "valid decimal parsing");
        rejects([] { checked_product(UINT64_MAX, 2, "test"); });
        require(json_string("a\"b\n\\") == "\"a\\\"b\\u000a\\\\\"", "JSON escaping");
        require(!identifier("../bad") && identifier("broad_tag"), "scenario identifier");
        for (double value : {0.0, 0.0001, 0.001, 0.1, 1.0, 1.2345, 10.0, 125.25, 1000000.0})
        {
            LatencyHistogram histogram;
            histogram.add(value);
            const double quantile = histogram.quantile(0.99);
            require(quantile >= value && quantile <= std::max(std::exp2(-10), value * std::exp2(1.0 / 256)),
                    "histogram precision guarantee");
        }
        LatencyHistogram histogram;
        for (int i = 1; i <= 100; ++i)
            histogram.add(i);
        require(histogram.quantile(0.5) >= 50 && histogram.quantile(0.5) < 50.14 &&
                    histogram.quantile(0.99) >= 99 && histogram.quantile(0.99) < 99.28,
                "nearest-rank histogram quantiles");
        rejects([&] { histogram.add(-1); });
        rejects([&] { histogram.add(std::numeric_limits<double>::infinity()); });
        std::cout << "header unit tests passed\n";
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

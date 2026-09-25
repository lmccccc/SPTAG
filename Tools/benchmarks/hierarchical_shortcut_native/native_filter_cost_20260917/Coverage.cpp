#define main unused_benchmark_main
#include "SpannAclBench.cpp"
#undef main
#include "inc/Core/SPANN/LimitedTagSupport.h"

int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("Expected native coverage INI");
        Helper::IniReader config, loader;
        if (config.LoadIniFile(argv[1]) != ErrorCode::Success)
            throw std::runtime_error("Cannot load coverage INI");
        const auto& params = config.GetParameters("Coverage");
        const auto directory = params.at("index");
        if (loader.LoadIniFile(directory + "/indexloader.ini") != ErrorCode::Success)
            throw std::runtime_error("Cannot load persisted schema");
        const auto& build = loader.GetParameters("BuildSSDIndex");
        if (build.at("columntypes") != "categorical,numeric")
            throw std::runtime_error("Unexpected native schema");
        std::vector<std::uint32_t> attributes, encoded;
        std::size_t count, columns, queries, width;
        if (!ReadNpyMatrix(params.at("attributes"), "<u4", attributes, count, columns) ||
            !ReadNpyMatrix(params.at("predicate"), "<u4", encoded, queries, width) ||
            count != 1000000 || columns != 2 || queries != 1000 || width != 8)
            throw std::runtime_error("Invalid native coverage input");
        Cache::DNFPredicate predicate;
        Cache::DNFClause clause;
        Cache::DNFLiteral literal;
        literal.col = std::stoul(params.at("column"));
        literal.val = std::stoul(params.at("bound"));
        literal.kind = 1;
        literal.op = Cache::DNF_LE;
        clause.lits.push_back(literal);
        predicate.clauses.push_back(clause);
        if (predicate.Empty() || !predicate.HasNumericLiteral())
            throw std::runtime_error("Coverage predicate is not real native numeric DNF");
        for (std::size_t q = 0; q < queries; ++q) {
            const auto* words = encoded.data() + q * width;
            if (words[0] != 7 || words[1] != 0x444e4633 || words[2] != 1 || words[3] != 1 ||
                words[4] != literal.kind || words[5] != literal.col || words[6] != literal.op ||
                words[7] != literal.val)
                throw std::runtime_error("Coverage and actual benchmark DNF differ");
        }
        for (std::size_t i = 0; i < count; ++i)
            if (!predicate.Matches(attributes.data() + i * columns, columns))
                throw std::runtime_error("Dataset record fails native predicate");
        std::ifstream mapping(directory + "/SPTAGHeadVectorIDs.bin", std::ios::binary);
        std::int32_t mapShape[2] = {};
        mapping.read(reinterpret_cast<char*>(mapShape), sizeof(mapShape));
        if (!mapping || mapShape[0] != 160091 || mapShape[1] != 1)
            throw std::runtime_error("Invalid head map");
        const auto heads = mapShape[0];
        SPANN::LimitedTagSupport support;
        std::string error;
        if (!support.Load(directory + "/" + build.at("limitedtagsupportfile"), heads,
            std::stoi(build.at("limitedtagslotsperhead")), std::stoi(build.at("limitedtagminheadcount")),
            std::stoi(build.at("limitedtagcolumn")), columns,
            std::stoull(params.at("generation")), &error))
            throw std::runtime_error("Native support loader: " + error);
        for (std::uint64_t head = 0; head < heads; ++head) {
            std::uint64_t id;
            mapping.read(reinterpret_cast<char*>(&id), sizeof(id));
            const auto* own = support.HeadAttributes(head);
            if (!mapping || id >= count || !own ||
                !std::equal(own, own + columns, attributes.data() + id * columns) ||
                !predicate.Matches(own, columns))
                throw std::runtime_error("Persisted native own attributes fail coverage or identity");
        }
        std::cout << "COVERAGE_NATIVE records=" << count << " own_heads=" << heads
                  << " predicate_empty=" << predicate.Empty() << " numeric=" << predicate.HasNumericLiteral()
                  << " column=" << literal.col << " upper_bound=" << literal.val
                  << " persisted_own_attributes_equal=1\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\n";
        return 2;
    }
}

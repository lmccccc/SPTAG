"""Causal Phase2 change: revision-protected compact intrinsic qualification metadata."""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_ratio_phase1_20260917/source"


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one integration site: {old[:100]}")
    return text.replace(old, new)


def main():
    if (HERE / "Supplier.h").exists():
        raise RuntimeError("Preserve materialized Phase2 source")
    t = (PARENT / "AnnService/inc/Core/Common/VersionLabel.h").read_text()
    t = once(t, "#include <atomic>", "#include <atomic>\n#include <shared_mutex>\n#include <mutex>")
    t = once(t, "Dataset<std::uint8_t> m_data;", """Dataset<std::uint8_t> m_data;
            mutable std::shared_mutex m_intrinsicMutex;
            std::uint64_t m_intrinsicRevision = 0;
            inline static std::atomic<std::uint64_t> s_intrinsicIdentity{0};
            const std::uint64_t m_intrinsicIdentity = ++s_intrinsicIdentity;""")
    t = once(t, "            VersionLabel()", """            auto IntrinsicReadGuard() const {
                return std::shared_lock<std::shared_mutex>(m_intrinsicMutex);
            }
            std::uint64_t IntrinsicRevision() const { return m_intrinsicRevision; }
            std::uint64_t IntrinsicIdentity() const { return m_intrinsicIdentity; }

            VersionLabel()""")
    for signature in (
        "void Initialize(SizeType size, SizeType blockSize, SizeType capacity)",
        "inline bool Delete(const SizeType& key)",
        "inline void SetVersion(const SizeType& key, const uint8_t& version)",
        "inline bool IncVersion(const SizeType& key, uint8_t* newVersion)",
        "inline ErrorCode Load(std::shared_ptr<Helper::DiskIO> input, SizeType blockSize, SizeType capacity)",
        "inline ErrorCode Load(char* pmemoryFile, SizeType blockSize, SizeType capacity)",
        "inline ErrorCode AddBatch(SizeType num)",
        "inline void SetR(SizeType num)",
    ):
        anchor = signature + "\n            {"
        t = once(t, anchor, anchor + """
                std::unique_lock<std::shared_mutex> intrinsicWrite(m_intrinsicMutex);
                ++m_intrinsicRevision;""")
    (HERE / "VersionLabel.h").write_text(t)
    t = (PARENT / "AnnService/inc/Core/SPANN/ExtraStaticSearcher.h").read_text()
    t = "#include <shared_mutex>\n#include <mutex>\n" + t
    anchor = "            virtual bool LoadIndex(Options& p_opt, COMMON::VersionLabel& p_versionMap, COMMON::Dataset<std::uint64_t>& p_vectorTranslateMap,  std::shared_ptr<VectorIndex> m_index) {"
    t = once(t, anchor, """            auto IntrinsicReadGuard() const {
                return std::shared_lock<std::shared_mutex>(m_intrinsicMutex);
            }
            std::uint64_t IntrinsicRevision() const { return m_intrinsicRevision; }
            std::uint64_t IntrinsicIdentity() const { return m_intrinsicIdentity; }

""" + anchor + """
                std::unique_lock<std::shared_mutex> intrinsicWrite(m_intrinsicMutex);
                ++m_intrinsicRevision;""")
    t = once(t, "std::vector<ListInfo> m_listInfos;", """std::vector<ListInfo> m_listInfos;
            mutable std::shared_mutex m_intrinsicMutex;
            std::uint64_t m_intrinsicRevision = 0;
            inline static std::atomic<std::uint64_t> s_intrinsicIdentity{0};
            const std::uint64_t m_intrinsicIdentity = ++s_intrinsicIdentity;""")
    (HERE / "ExtraStaticSearcher.h").write_text(t)
    t = (PARENT / "AnnService/src/Core/SPANN/SPANNIndex.cpp").read_text()
    t = '#include "IntrinsicValidity.h"\n' + t
    anchor = "                const auto qualification = [&](int head, bool postingAdmission) {"
    t = once(t, anchor, """                std::shared_lock<std::shared_mutex> postingGuard, versionGuard;
                const unsigned char* intrinsic = nullptr;
                const auto intrinsicBegin = ShortcutFull::profile ? std::chrono::steady_clock::now()
                    : std::chrono::steady_clock::time_point{};
                if (ShortcutFull::intrinsicCache) {
                    if (m_mutableLimitedTagLayout)
                        throw std::runtime_error("Intrinsic cache requires read-only head/posting identity");
                    auto* staticPostings = dynamic_cast<const ExtraStaticSearcher<T>*>(m_extraSearcher.get());
                    if (!staticPostings) throw std::runtime_error("Intrinsic cache requires static postings");
                    postingGuard = staticPostings->IntrinsicReadGuard();
                    versionGuard = m_versionMap.IntrinsicReadGuard();
                    static thread_local H1Supplier::IntrinsicValidity cache;
                    intrinsic = cache.Ensure({
                        &m_vectorTranslateMap, m_versionMap.IntrinsicIdentity(), m_versionMap.IntrinsicRevision(),
                        staticPostings->IntrinsicIdentity(), staticPostings->IntrinsicRevision(),
                        m_vectorTranslateMap.R()}, [&](int head) {
                            const auto mapped = *m_vectorTranslateMap[head];
                            if (mapped >= static_cast<std::uint64_t>(m_versionMap.Count()))
                                throw std::runtime_error("Invalid intrinsic-cache canonical VID");
                            return (m_extraSearcher->CheckValidPosting(head, nullptr) ? 1 : 0) |
                                (!m_versionMap.Deleted(static_cast<SizeType>(mapped)) ? 2 : 0);
                        });
                    ShortcutFull::Last().supplyIntrinsicBytes = cache.flags.size();
                }
                if (ShortcutFull::profile)
                    ShortcutFull::Last().supplyIntrinsicNs =
                        std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - intrinsicBegin).count();
""" + anchor)
    anchor = """                    const auto mapped = *m_vectorTranslateMap[head];
                    if (mapped >= static_cast<std::uint64_t>(m_versionMap.Count()))
                        throw std::runtime_error("Invalid degree16 canonical VID");
                    const bool ownEligible = !m_versionMap.Deleted(static_cast<SizeType>(mapped)) &&
                        (!hasExactFilter || limitedHeadMatchesExactPredicate(head));
                    const bool postingEligible = postingAdmission &&
                        m_extraSearcher->CheckValidPosting(head, nullptr) &&
                        (useLimitedTagPure || !candidatePostingFilter || candidatePostingFilter(head));"""
    t = once(t, anchor, """                    if (intrinsic != nullptr) {
                        const unsigned char bits = intrinsic[head];
                        const bool ownEligible = (bits & 2) &&
                            (!hasExactFilter || limitedHeadMatchesExactPredicate(head));
                        const bool postingEligible = postingAdmission && (bits & 1) &&
                            (useLimitedTagPure || !candidatePostingFilter || candidatePostingFilter(head));
                        return (postingEligible ? 1 : 0) | (ownEligible ? 2 : 0);
                    }
""" + anchor)
    (HERE / "SPANNIndex.cpp").write_text(t)
    t = (PARENT / "AnnService/Supplier.h").read_text()
    t = once(t, "profiling = profileOn; trace = profileOn || capturing;",
             "profiling = false; trace = profileOn || capturing;")
    (HERE / "Supplier.h").write_text(t)
    t = (PARENT / "AnnService/NativeAdapter.h").read_text()
    start = "                  Admit admission, Own own, Qualify qualify, Signature signature) {"
    t = once(t, start, start + """
    const auto prepareBegin = ShortcutFull::profile ? std::chrono::steady_clock::now()
        : std::chrono::steady_clock::time_point{};""")
    anchor = "    try {\n        SPTAG::COMMON::QueryResultSet<T> spatial"
    t = once(t, anchor, """    const auto searchBegin = ShortcutFull::profile ? std::chrono::steady_clock::now()
        : std::chrono::steady_clock::time_point{};
""" + anchor)
    anchor = """    bkt->BenchmarkRestore(std::move(original));
    Active() = nullptr; ShortcutBench::active = nullptr;"""
    t = once(t, anchor, """    const auto finishBegin = ShortcutFull::profile ? std::chrono::steady_clock::now()
        : std::chrono::steady_clock::time_point{};
""" + anchor)
    anchor = "        o.connectivityAudits = e.connectivityAudits;\n    }\n}"
    t = once(t, anchor, """        o.connectivityAudits = e.connectivityAudits;
    }
    if (ShortcutFull::profile) {
        o.supplyPrepareNs = std::chrono::duration_cast<std::chrono::nanoseconds>(searchBegin - prepareBegin).count();
        o.supplySearchNs = std::chrono::duration_cast<std::chrono::nanoseconds>(finishBegin - searchBegin).count();
        o.supplyFinishNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - finishBegin).count();
    }
}""")
    (HERE / "NativeAdapter.h").write_text(t)
    t = (PARENT / "AnnService/FullHooks.h").read_text()
    t = once(t, "inline bool optimized = true;", "inline bool optimized = true;\ninline bool intrinsicCache = false;")
    fields = ("IntrinsicNs", "IntrinsicBytes", "PrepareNs", "SearchNs", "FinishNs")
    t = once(t, "struct Observation {", "struct Observation {\n    std::uint64_t " +
             ", ".join("supply" + f + " = 0" for f in fields) + ";")
    t = once(t, 'p.first != "shortcuthotpath")', 'p.first != "shortcuthotpath" && p.first != "shortcutintrinsiccache")')
    t = once(t, 'optimized = hotpath == "optimized";',
             'optimized = hotpath == "optimized";\n        intrinsicCache = Boolean(get("shortcutintrinsiccache"));')
    (HERE / "FullHooks.h").write_text(t)
    t = (PARENT / "Tools/benchmarks/SpannAclBench.cpp").read_text()
    t = once(t, '                     << ",\\"supplyGraph\\":" << o.supplyGraph',
             "".join(f'                     << ",\\"supply{f}\\":" << o.supply{f}\n' for f in fields) +
             '                     << ",\\"supplyGraph\\":" << o.supplyGraph')
    (HERE / "SpannAclBench.cpp").write_text(t)
    for name in ("Signature.h", "BKTIndex.cpp", "SupplierTests.cpp", "CMakeLists.txt"):
        source = HERE.parent / "ratio_degree_phase1" / name
        shutil.copy2(source, HERE / name)
    print("Materialized compact intrinsic validity, complete mutation revisions, read guards, coarse-only profiling.")


if __name__ == "__main__":
    main()

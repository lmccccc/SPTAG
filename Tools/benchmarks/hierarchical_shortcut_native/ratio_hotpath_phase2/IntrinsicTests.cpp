#include "IntrinsicValidity.h"
#include "inc/Core/Common/VersionLabel.h"
#include <future>
#include <iostream>

void Check(bool value) {
    if (!value) throw std::runtime_error("Intrinsic validity fixture failed");
}

int main() {
    SPTAG::COMMON::VersionLabel versions;
    versions.Initialize(4, 4, 16);
    for (int i = 0; i < 4; ++i) versions.SetVersion(i, 0);
    H1Supplier::IntrinsicValidity cache;
    unsigned char postings[] = {1, 0, 1, 0};
    std::uint64_t layout = 0;
    auto snapshot = [&] {
        auto guard = versions.IntrinsicReadGuard();
        cache.Ensure({&versions, versions.IntrinsicIdentity(), versions.IntrinsicRevision(),
                      1, layout, static_cast<int>(versions.Count())},
                     [&](int id) { return postings[id] | (versions.Deleted(id) ? 0 : 2); });
    };
    snapshot();
    Check((cache.flags == std::vector<unsigned char>{3, 2, 3, 2}));
    snapshot(); Check(cache.rebuilds == 1);
    versions.Delete(0); snapshot(); Check(cache.flags[0] == 1 && cache.rebuilds == 2);
    versions.Delete(1); snapshot(); Check(cache.flags[1] == 0);
    versions.SetVersion(1, 0); snapshot(); Check(cache.flags[1] == 2);
    std::uint8_t next;
    const auto before = cache.rebuilds;
    Check(versions.IncVersion(1, &next)); snapshot();
    Check(cache.flags[1] == 2 && cache.rebuilds == before + 1);
    postings[1] = 1; ++layout; snapshot(); Check(cache.flags[1] == 3);
    {
        auto guard = versions.IntrinsicReadGuard();
        const auto revision = versions.IntrinsicRevision();
        std::promise<void> entered;
        auto started = entered.get_future();
        auto writer = std::async(std::launch::async, [&] {
            entered.set_value();
            versions.SetVersion(1, 0xfe);
        });
        started.get();
        Check(writer.wait_for(std::chrono::milliseconds(20)) == std::future_status::timeout);
        Check(versions.IntrinsicRevision() == revision);
        guard.unlock();
        writer.get();
    }
    snapshot(); Check(cache.flags[1] == 1);
    versions.SetR(3); snapshot(); Check(cache.flags.size() == 3);
    Check(versions.AddBatch(1) == SPTAG::ErrorCode::Success);
    versions.SetVersion(3, 0); snapshot(); Check(cache.flags.size() == 4 && cache.flags[3] == 2);
    SPTAG::COMMON::VersionLabel another;
    Check(another.IntrinsicIdentity() != versions.IntrinsicIdentity());
    bool rejected = false;
    try {
        cache.Ensure({nullptr, 0, 0, 0, 0, 1}, [](int) { return 4; });
    } catch (const std::runtime_error&) { rejected = true; }
    Check(rejected && !cache.ready);
    std::cout << "Intrinsic cache: own-only, deleted, undeleted, version, layout, resize, "
                 "identity and writer exclusion fixtures passed\n";
}

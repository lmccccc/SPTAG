// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/Common.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

namespace SPTAG { namespace Helper {

class BuildProgress
{
public:
    struct Work {
        std::uint64_t checked = 0, maxChecked = 0, direct = 0, fallbacks = 0, exactDistances = 0;
        void Add(const Work& other) {
            checked += other.checked;
            maxChecked = (std::max)(maxChecked, other.maxChecked);
            direct += other.direct;
            fallbacks += other.fallbacks;
            exactDistances += other.exactDistances;
        }
    };

    explicit BuildProgress(std::string stage, std::uint64_t total = 0,
                           std::chrono::milliseconds interval = std::chrono::seconds(30))
        : m_stage(std::move(stage)), m_total(total), m_started(Clock::now())
    {
        if (interval.count() <= 0) throw std::invalid_argument("Invalid build progress interval");
        Report("started");
        m_reporter = std::thread([this, interval] {
            std::unique_lock<std::mutex> lock(m_mutex);
            while (!m_wakeup.wait_for(lock, interval, [this] { return m_stop; })) {
                lock.unlock();
                Report("running");
                lock.lock();
            }
        });
    }

    ~BuildProgress() {
        Stop();
        if (!m_finished) Report("incomplete");
    }

    void Advance(std::uint64_t count, const Work& work) {
        if (work.checked) m_checked.fetch_add(work.checked, std::memory_order_relaxed);
        if (work.direct) m_direct.fetch_add(work.direct, std::memory_order_relaxed);
        if (work.fallbacks) m_fallbacks.fetch_add(work.fallbacks, std::memory_order_relaxed);
        if (work.exactDistances) m_exact.fetch_add(work.exactDistances, std::memory_order_relaxed);
        auto maximum = m_maxChecked.load(std::memory_order_relaxed);
        while (maximum < work.maxChecked && !m_maxChecked.compare_exchange_weak(
            maximum, work.maxChecked, std::memory_order_relaxed)) {}
        m_done.fetch_add(count, std::memory_order_relaxed);
    }
    void Advance(std::uint64_t count = 1) { Advance(count, Work{}); }

    void Finish() {
        Stop();
        if (m_total && m_done.load() != m_total)
            throw std::runtime_error("Incomplete build progress: " + m_stage);
        m_finished = true;
        Report("complete");
    }

private:
    using Clock = std::chrono::steady_clock;
    void Stop() {
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_stop = true;
        }
        m_wakeup.notify_one();
        if (m_reporter.joinable()) m_reporter.join();
    }
    void Report(const char* state) const {
        const auto done = m_done.load(std::memory_order_relaxed);
        const double elapsed = std::chrono::duration<double>(Clock::now() - m_started).count();
        const double rate = elapsed > 0 ? done / elapsed : 0;
        const double eta = m_total && done && done <= m_total && rate > 0
            ? (m_total - done) / rate : -1;
        SPTAGLIB_LOG(LogLevel::LL_Info,
            "[BuildProgress] stage=%s state=%s done=%llu total=%llu percent=%.2f "
            "elapsedSeconds=%.3f itemsPerSecond=%.3f stageEtaSeconds=%.3f "
            "checked=%llu maxChecked=%llu directQueries=%llu exactFallbacks=%llu exactDistances=%llu\n",
            m_stage.c_str(), state, static_cast<unsigned long long>(done),
            static_cast<unsigned long long>(m_total), m_total ? 100.0 * done / m_total : -1,
            elapsed, rate, eta, static_cast<unsigned long long>(m_checked.load()),
            static_cast<unsigned long long>(m_maxChecked.load()),
            static_cast<unsigned long long>(m_direct.load()),
            static_cast<unsigned long long>(m_fallbacks.load()),
            static_cast<unsigned long long>(m_exact.load()));
    }
    const std::string m_stage;
    const std::uint64_t m_total;
    const Clock::time_point m_started;
    std::atomic<std::uint64_t> m_done{0}, m_checked{0}, m_maxChecked{0}, m_direct{0}, m_fallbacks{0}, m_exact{0};
    std::mutex m_mutex;
    std::condition_variable m_wakeup;
    bool m_stop = false, m_finished = false;
    std::thread m_reporter;
};

}}

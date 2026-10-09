// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once
#include <cstdint>

namespace SPTAG { namespace COMMON {

// Observe only fresh, scored H1 navigation candidates, never posting members.
class PostingMatchRate
{
public:
    PostingMatchRate(double percent, int window) : m_percent(percent), m_window(window) {}
    bool Enabled() const { return m_percent > 0; }
    bool Triggered() const { return m_triggered; }
    std::uint64_t Samples() const { return m_samples; }
    std::uint64_t Matches() const { return m_lastMatches; }
    std::uint64_t Windows() const { return m_windows; }

    void Observe(bool match) {
        if (!Enabled() || m_triggered) return;
        ++m_samples;
        ++m_count;
        m_matches += match;
        if (m_count != m_window) return;
        ++m_windows;
        m_lastMatches = m_matches;
        m_triggered = m_matches * 100 < std::uint64_t(m_window) * m_percent;
        m_count = 0;
        m_matches = 0;
    }

private:
    double m_percent;
    int m_window;
    int m_count = 0;
    std::uint64_t m_samples = 0, m_matches = 0, m_lastMatches = 0, m_windows = 0;
    bool m_triggered = false;
};

}}

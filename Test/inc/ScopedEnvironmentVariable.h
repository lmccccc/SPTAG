// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once

#include <cstdlib>
#include <string>

class ScopedEnvironmentVariable
{
public:
    ScopedEnvironmentVariable(const char* p_name, const char* p_value)
        : m_name(p_name)
    {
        const char* previous = std::getenv(p_name);
        if (previous != nullptr) {
            m_hadPrevious = true;
            m_previous = previous;
        }
        Set(p_value);
    }

    ~ScopedEnvironmentVariable()
    {
        Set(m_hadPrevious ? m_previous.c_str() : nullptr);
    }

    void Set(const char* p_value)
    {
#ifdef _WIN32
        _putenv_s(m_name.c_str(), p_value == nullptr ? "" : p_value);
#else
        if (p_value == nullptr) {
            unsetenv(m_name.c_str());
        } else {
            setenv(m_name.c_str(), p_value, 1);
        }
#endif
    }

private:
    std::string m_name;
    std::string m_previous;
    bool m_hadPrevious = false;
};

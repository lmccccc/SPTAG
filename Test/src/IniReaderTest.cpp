// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Helper/SimpleIniReader.h"
#include "inc/Core/SPANN/Options.h"
#include "inc/Core/SPANN/IExtraSearcher.h"
#include "inc/ScopedEnvironmentVariable.h"
#include "inc/Test.h"

#include <algorithm>
#include <cctype>
#include <fstream>

BOOST_AUTO_TEST_SUITE(IniReaderTest)

BOOST_AUTO_TEST_CASE(IniReaderLoadTest)
{
    std::ofstream tmpIni("temp.ini");
    tmpIni << "[Common]" << std::endl;
    tmpIni << "; Comment " << std::endl;
    tmpIni << "Param1=1" << std::endl;
    tmpIni << "Param2=Exp=2" << std::endl;

    tmpIni.close();

    SPTAG::Helper::IniReader reader;
    BOOST_CHECK(SPTAG::ErrorCode::Success == reader.LoadIniFile("temp.ini"));

    BOOST_CHECK(reader.DoesSectionExist("Common"));
    BOOST_CHECK(reader.DoesParameterExist("Common", "Param1"));
    BOOST_CHECK(reader.DoesParameterExist("Common", "Param2"));

    BOOST_CHECK(!reader.DoesSectionExist("NotExist"));
    BOOST_CHECK(!reader.DoesParameterExist("NotExist", "Param1"));
    BOOST_CHECK(!reader.DoesParameterExist("Common", "ParamNotExist"));

    BOOST_CHECK(1 == reader.GetParameter<int>("Common", "Param1", 0));
    BOOST_CHECK(0 == reader.GetParameter<int>("Common", "ParamNotExist", 0));

    BOOST_CHECK(std::string("Exp=2") == reader.GetParameter<std::string>("Common", "Param2", std::string()));
    BOOST_CHECK(std::string("1") == reader.GetParameter<std::string>("Common", "Param1", std::string()));
    BOOST_CHECK(std::string() == reader.GetParameter<std::string>("Common", "ParamNotExist", std::string()));
}

BOOST_AUTO_TEST_CASE(RemovedPostingRuntimeParametersRejectExplicitDefaults)
{
    SPTAG::SPANN::Options options;
    for (const char* name : {"EnableUnfilterTail", "UnfilterPurePages", "UnfilterExtraTailPages",
                             "UnfilterPureDistanceScanPercent", "AblateUExtra", "AblateTail"}) {
        std::string lowercase(name);
        std::transform(lowercase.begin(), lowercase.end(), lowercase.begin(),
            [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
        for (const char* key : {name, lowercase.c_str()}) {
            for (const char* section : {"Base", "SelectHead", "BuildHead", "BuildSSDIndex", "SearchSSDIndex"}) {
                for (const char* value : {"", "false", "0", "true", "1", "100"}) {
                    BOOST_TEST_CONTEXT(section << "." << key << "=" << value) {
                        BOOST_CHECK(options.SetParameter(section, key, value) ==
                            SPTAG::ErrorCode::FailedParseValue);
                    }
                }
            }
        }
    }
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "TailReplicaCount", "2") ==
        SPTAG::ErrorCode::Success);
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "UnfilterTailBufferLength", "1") ==
        SPTAG::ErrorCode::Success);
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "SearchPostingPageLimit", "2") ==
        SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(options.m_searchPostingPageLimit, 2);
    for (const char* name : {"EnableOrderedPageStart", "OrderedPageStartAttrs"}) {
        BOOST_CHECK(options.SetParameter("SearchSSDIndex", name, "0") ==
            SPTAG::ErrorCode::FailedParseValue);
    }
}

BOOST_AUTO_TEST_CASE(RemovedPostingRuntimeEnvironmentRejectsPresence)
{
    for (const char* name : {"SPTAG_OPQ_PREFILTER", "SPTAG_PAGE_SELECT", "SPTAG_PAGE_DIAG",
                             "SPTAG_DNF_NODROP", "SPTAG_RBQ_EXHAUSTIVE",
                             "SPTAG_UNFILTER_TAIL", "SPTAG_UNFILTER_PURE_PAGES",
                             "SPTAG_UNFILTER_EXTRA_TAIL_PAGES", "SPTAG_UNFILTER_PURE_DISTANCE_SCAN_PERCENT",
                             "SPTAG_ABLATE_UEXTRA", "SPTAG_ABLATE_TAIL"}) {
        for (const char* value : {"0", "false", "1"}) {
            BOOST_TEST_CONTEXT(name << "=" << value) {
                ScopedEnvironmentVariable environment(name, value);
                BOOST_CHECK(!SPTAG::SPANN::Options::ValidatePostingRuntimeEnvironment());
                BOOST_CHECK(!SPTAG::SPANN::Options::ValidateNativeEnvironment());
            }
        }
#ifndef _WIN32
        ScopedEnvironmentVariable empty(name, "");
        BOOST_CHECK(!SPTAG::SPANN::Options::ValidatePostingRuntimeEnvironment());
#endif
    }
}

BOOST_AUTO_TEST_CASE(PostingRegionsSharePhysicalPageLimit)
{
    using Range = SPTAG::SPANN::ExtraWorkSpace::PostingReadRange;
    constexpr int offset = 200;
    constexpr int recordBytes = 520;
    Range h;
    h.SetContiguousRecordRange(offset, 0, 8, recordBytes);
    h.LimitContiguousPages(1, offset, recordBytes);
    BOOST_CHECK_EQUAL(h.m_readStartPage, 0);
    BOOST_CHECK_EQUAL(h.m_readPageCount, 1);
    BOOST_CHECK_EQUAL(h.m_scanBegin, 0);
    BOOST_CHECK_EQUAL(h.m_scanEnd, 7);

    Range o;
    o.SetContiguousRecordRange(offset, 8, 20, recordBytes);
    o.LimitContiguousPages(1, offset, recordBytes);
    BOOST_CHECK_EQUAL(o.m_readStartPage, 1);
    BOOST_CHECK_EQUAL(o.m_readPageCount, 1);
    BOOST_CHECK_EQUAL(o.m_scanBegin, 8);
    BOOST_CHECK_EQUAL(o.m_scanEnd, 15);

    Range legacyTail;
    legacyTail.SetContiguousRecordRange(offset, 0, 20, recordBytes);
    legacyTail.LimitContiguousPages(1, offset, recordBytes);
    BOOST_CHECK_EQUAL(legacyTail.ScanCount(), h.ScanCount());
    legacyTail.SetContiguousRecordRange(offset, 0, 20, recordBytes);
    legacyTail.LimitContiguousPages(2, offset, recordBytes);
    BOOST_CHECK_EQUAL(legacyTail.m_readPageCount, 2);
    BOOST_CHECK_EQUAL(legacyTail.m_scanEnd, 15);

    Range straddling;
    straddling.SetContiguousRecordRange(4000, 0, 1, recordBytes);
    straddling.LimitContiguousPages(1, 4000, recordBytes);
    BOOST_CHECK_EQUAL(straddling.ScanCount(), 0);
    BOOST_CHECK_EQUAL(straddling.m_readPageCount, 0);
}

BOOST_AUTO_TEST_SUITE_END()
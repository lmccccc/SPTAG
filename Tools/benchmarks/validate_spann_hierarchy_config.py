#!/usr/bin/env python3
"""Validate native hierarchy settings without opening datasets or starting a build."""

import configparser
import math
import os
import sys
from pathlib import Path


def read_config(path):
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
    parser.read_string(Path(path).read_text())
    return {section.lower(): dict(parser[section]) for section in parser.sections()}


def canonical(section, key):
    if section == "searchssdindex" and key == "postingpagelimit":
        return "searchpostingpagelimit"
    if section == "selecthead":
        if key == "selectsecondlevel":
            return "hierarchyenabled"
        if key == "secondlevelhierarchylevels":
            return "hierarchylevels"
    if section in {"selecthead", "buildssdindex", "searchssdindex"}:
        if key.startswith("secondlevel") and key != "secondlevelratio":
            return "hierarchy" + key[len("secondlevel"):]
    return key


def normalized(config):
    result = {}
    for section, values in config.items():
        result[section] = {}
        for key, value in values.items():
            name = canonical(section, key)
            if name == key or name not in values:
                result[section][name] = value
    return result


def column_types(config, runtime=False):
    values = config.get("buildssdindex" if runtime else "tags", {})
    text = values.get("columntypes", "")
    if text:
        aliases = {"cate": "categorical", "num": "numeric",
                   "categorical": "categorical", "numeric": "numeric"}
        try:
            types = [aliases[t.strip().lower()] for t in text.split(",")]
        except KeyError as error:
            raise ValueError("ColumnTypes requires categorical or numeric for every original column") from error
    elif runtime:
        width = int(values.get("numtagspervec", "0"))
        categories = int(values.get("staticacltagcols", "0")) or width
        if not 0 <= categories <= width:
            raise ValueError("Invalid legacy tag schema")
        types = ["categorical"] * categories + ["numeric"] * (width - categories)
    else:
        types = []
    width = int(values.get("numtagspervec", str(len(types))))
    if width != len(types) or not 0 <= width <= 2147483647 // 4:
        raise ValueError("NumTagsPerVec must equal the ColumnTypes width")
    if runtime and text:
        if values.get("tagschemaversion") != "1":
            raise ValueError("Unsupported or missing stored tag schema version")
        fingerprint = 1469598103934665603
        for byte in ("TagSchema1:" + ",".join(types)).encode():
            fingerprint = ((fingerprint ^ byte) * 1099511628211) & ((1 << 64) - 1)
        if values.get("tagschemafingerprint") != str(fingerprint):
            raise ValueError("Stored tag schema fingerprint mismatch")
    elif runtime and (values.get("tagschemafingerprint") or values.get("tagschemaversion", "0") != "0"):
        raise ValueError("Stored tag schema is missing ColumnTypes")
    return types


def validate(config, *, runtime=False):
    removed = {"aclcols", "hierlevelwidths", "pivotforcenodecount",
               "disablepivotestimator", "routingcols", "pervectortagsfile",
               "numericcols", "enableextremesparsetag", "extremesparsetagmincount",
               "extremesparsetagfile", "logextremesparsetagroute", "filterkeepcross",
               "disablecrosssubgraph", "unifiednprobebudget", "multinodebudgetkeepratio",
               "loguextra", "postingquantbits", "hybridgraphdegree", "enablehierpostingfilter",
               "limitedtagvoteheadcount", "limitedtagmaxexpandedpostingpages", "limitedtagmaxextrasupports",
               "sparsefallbackmaxheads", "sparsefallbackmaxpostingpages",
               "hierarchyrouteselectivitythreshold", "secondlevelrouteselectivitythreshold",
               "vectoroffset", "vectorcount", "withmetaindex", "sharebuildownership", "tagoffset",
               "bktseed", "tptseed", "hierarchysignatureminselectivity", "hierarchysignaturemaxselectivity",
               "secondlevelsignatureminselectivity", "secondlevelsignaturemaxselectivity",
               "hierarchygraphsignaturepruning", "secondlevelgraphsignaturepruning",
               "headnavigationmode", "forcedensetagsearch", "directsparsemaxpostings",
               "filteredsearchnprobesafety", "filteredsearchtargetrecall", "filteredsearchcoverageexponent",
               "enableadaptivefilterednprobe", "logadaptivenprobe", "filterkeepuextra",
               "buildprimaryheadcsr", "primaryheadcsrfile", "enableprimaryheadbypass", "primaryheadbypassrerankl",
               "hybridroutesamplecount", "hybridrouteselectivitythreshold", "hybridroutedeformationthreshold",
               "loghybridroute",
               "enableunfiltertail", "unfilterpurepages", "unfilterextratailpages",
               "unfilterpuredistancescanpercent", "ablateuextra", "ablatetail"}
    for section, values in config.items():
        for key, value in values.items():
            section_alias = section == "multitenant" and key in {
                "crossedges", "crossextraedges", "dualpoolaugment", "dualpoolextraratio", "uextraidfile"}
            runtime_layout = section == "searchssdindex" and key in {
                "enableorderedpagestart", "orderedpagestartattrs"}
            derived_bulk = not runtime and (
                (section == "tags" and key == "tenant") or
                (section in {"buildssdindex", "searchssdindex"} and key == "numtagspervec") or
                key == "staticacltagcols")
            if not runtime and key == "columntypes" and section != "tags":
                raise ValueError("Specify ColumnTypes only in [Tags]")
            if key in removed or section_alias or runtime_layout or derived_bulk or (key in {"selectheadtype", "selecttype"} and
                                  value.lower() == "pertagbkt"):
                raise ValueError(f"[{section}] {key} was removed; use canonical native options")
    for key in ("SPTAG_ACL_COLS", "SPTAG_HIER_LEVEL_WIDTHS", "SPTAG_PIVOT_FORCE_NODE_COUNT",
                "SPTAG_DISABLE_PIVOT_ESTIMATOR", "SPTAG_ROUTING_COLS", "SPTAG_ROUTING_ONLY",
                "SPTAG_PER_VECTOR_TAGS_FILE", "SPTAG_PERTAG_HEAD_RATIO", "SPTAG_SELECT_TYPE_OVERRIDE",
                "SPTAG_NUMERIC_COLS", "SPTAG_TAG_OFFSET", "SPTAG_TAGS_OFFSET",
                "SPTAG_OPQ_PREFILTER", "SPTAG_PAGE_SELECT", "SPTAG_PAGE_DIAG",
                "SPTAG_DNF_NODROP", "SPTAG_RBQ_EXHAUSTIVE",
                "SPTAG_UNFILTER_TAIL", "SPTAG_UNFILTER_PURE_PAGES", "SPTAG_UNFILTER_EXTRA_TAIL_PAGES",
                "SPTAG_UNFILTER_PURE_DISTANCE_SCAN_PERCENT", "SPTAG_ABLATE_UEXTRA", "SPTAG_ABLATE_TAIL"):
        if key in os.environ:
            raise ValueError(f"{key} was removed; use canonical native options")
    if not runtime and "SPTAG_BUILD_SHARE_OWNERSHIP" in os.environ:
        raise ValueError("SPTAG_BUILD_SHARE_OWNERSHIP was removed from bulk input; ownership is automatic")
    base = config.get("base", {})
    if base.get("vectortype", "DEFAULT").upper() not in {"DEFAULT", "TXT", "XVEC"}:
        raise ValueError("VectorType is a container (DEFAULT/TXT/XVEC), not an element type. "
                         "Headerless RAW is unsupported; migrate to a new native file explicitly.")
    if base.get("valuetype", "Float").lower() not in {"float", "int8", "uint8", "int16"}:
        raise ValueError("ValueType must be Float/Int8/UInt8/Int16")
    limit = int(base.get("vectorsize", "-1"))
    if limit < -1 or limit == 0 or limit > 2147483647:
        raise ValueError("VectorSize must be -1 or a positive SizeType prefix")
    dim = int(base.get("dim", "0"))
    if dim < 0 or dim > 2147483647 or (base.get("vectortype", "DEFAULT").upper() != "DEFAULT" and dim == 0):
        raise ValueError("Invalid Dim; TXT/XVEC require a positive dimension")
    if "normalized" in base and base["normalized"].lower() not in {"true", "false", "0", "1"}:
        raise ValueError("Normalized must be a native reader boolean (true/false/0/1)")
    tags = config.get("tags", {})
    types = column_types(config, runtime)
    columns = len(types)
    if not runtime and bool(tags.get("tagfile")) != (columns > 0):
        raise ValueError("TagFile and ColumnTypes must be supplied together")
    for section in ("buildssdindex", "searchssdindex"):
        if columns and ("limitedtagcolumn" in config.get(section, {}) or
                        (section == "buildssdindex" and config.get(section, {}).get(
                            "enablelimitedtagposting", "false").lower() in {"true", "1"})):
            key = int(config.get(section, {}).get("limitedtagcolumn", "0"))
            if not 0 <= key < columns or types[key] != "categorical":
                raise ValueError("LimitedTagColumn must identify a categorical original column")
            if section == "searchssdindex" and key != int(config.get(
                    "buildssdindex", {}).get("limitedtagcolumn", "0")):
                raise ValueError("Search overlay cannot change the constructed tag key column")
    select = normalized(config).get("selecthead", {})
    enabled = select.get("hierarchyenabled", "false").lower() in {"true", "1", "yes", "on"}
    if not enabled:
        return
    ratio = float(select.get("ratio", "0.2"))
    if not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("hierarchy selection requires 0 < Ratio < 1")
    if int(select.get("hierarchylevels", "2")) < 2:
        raise ValueError("HierarchyLevels counts H1 and must be >=2")
    if int(select.get("count", "0")) != 0:
        raise ValueError("hierarchy selection requires Count=0; use Ratio")
    if "secondlevelratio" in select:
        legacy = float(select["secondlevelratio"])
        if not math.isfinite(legacy) or not math.isclose(legacy, ratio, rel_tol=0, abs_tol=1e-12):
            raise ValueError("conflicting legacy SecondLevelRatio; every level now uses Ratio. "
                             "Use the legacy reader for unequal-ratio saved indexes, not an edited INI.")


def validate_runtime(config, runtime):
    validate(runtime, runtime=True)
    if column_types(config) != column_types(runtime, True):
        raise ValueError("Runtime ColumnTypes disagrees with the build schema")
    if int(config.get("buildssdindex", {}).get("limitedtagcolumn", "0")) != int(
            runtime.get("buildssdindex", {}).get("limitedtagcolumn", "0")):
        raise ValueError("Runtime LimitedTagColumn disagrees with the build key")
    expected = normalized(config)
    actual = normalized(runtime)
    for section in ("selecthead", "buildssdindex", "searchssdindex"):
        for key in runtime.get(section, {}):
            if key == "secondlevelratio" or canonical(section, key) != key:
                raise ValueError(f"runtime [{section}] still persists legacy key {key}")
        for key, value in expected.get(section, {}).items():
            if not (key.startswith("hierarchy") or
                    (section == "selecthead" and key in {"ratio", "buildh1graph"})):
                continue
            saved = actual.get(section, {}).get(key)
            if saved is None:
                raise ValueError(f"runtime [{section}] missing {key}")
            if value.lower() == saved.lower():
                continue
            if value.lower() in {"true", "false", "1", "0"} and saved.lower() in {"true", "false", "1", "0"}:
                if (value.lower() in {"true", "1"}) == (saved.lower() in {"true", "1"}):
                    continue
            try:
                if math.isclose(float(value), float(saved), rel_tol=1e-6, abs_tol=1e-9):
                    continue
            except ValueError:
                pass
            raise ValueError(f"runtime [{section}] {key}: expected {value}, got {saved}")


def main():
    if len(sys.argv) not in (2, 3):
        raise ValueError("usage: validate_spann_hierarchy_config.py build.ini [indexloader.ini]")
    config = read_config(sys.argv[1])
    validate(config)
    if len(sys.argv) == 3:
        validate_runtime(config, read_config(sys.argv[2]))
    print("[launcher] native hierarchy configuration verified")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, configparser.Error) as error:
        sys.exit(f"[launcher] {error}")

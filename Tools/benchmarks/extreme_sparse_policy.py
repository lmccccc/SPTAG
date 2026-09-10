#!/usr/bin/env python3
"""Derive a rare-label dataset recipe from active head/support/search budgets."""

import configparser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from native_vector_metadata import default_vector_shape


@dataclass(frozen=True)
class ExtremeSparsePolicy:
    vector_count: int
    head_ratio: Decimal
    slots_per_head: int
    coverage_target: int
    tag_file: str


def parse_ratio(value: str) -> Decimal:
    try:
        ratio = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"invalid ratio: {value}") from error
    if not ratio.is_finite():
        raise ValueError("ratio must be finite")
    return ratio


def coverage_boundary_count(
    vector_count: int,
    policy: ExtremeSparsePolicy,
) -> int:
    if vector_count <= 0:
        raise ValueError("vector_count must be positive")
    if policy.head_ratio <= 0 or policy.head_ratio > 1:
        raise ValueError("SelectHead Ratio must be in (0,1]")
    if policy.slots_per_head <= 0:
        raise ValueError(
            "LimitedTagSlotsPerHead must be positive"
        )
    if policy.coverage_target <= 0:
        raise ValueError("SearchSSDIndex InternalResultNum must be positive")
    coverage_max = int(
        (
            Decimal(policy.coverage_target)
            / (
                policy.head_ratio *
                Decimal(policy.slots_per_head)
            )
        ).to_integral_value(rounding=ROUND_CEILING)
    ) - 1
    count = coverage_max
    if count <= 0:
        raise ValueError(
            "coverage policy has no positive extreme-tag boundary"
        )
    if count >= vector_count:
        raise ValueError(
            "coverage-derived extreme tag leaves no vectors "
            "for Zipf values"
        )
    return count


def read_extreme_tag_policy(path: Path) -> ExtremeSparsePolicy:
    config = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8") as stream:
        config.read_file(stream)
    for removed in ("TagOffset", "StaticACLTagCols"):
        if any(removed in config[section] for section in config.sections()):
            raise ValueError("Use native uint32 TagFile with explicit [Tags] ColumnTypes")
    try:
        policy = ExtremeSparsePolicy(
            vector_count=default_vector_shape(config)[0],
            head_ratio=parse_ratio(
                config.get("SelectHead", "Ratio")
            ),
            slots_per_head=config.getint(
                "BuildSSDIndex", "LimitedTagSlotsPerHead", fallback=2
            ),
            coverage_target=config.getint(
                "SearchSSDIndex", "InternalResultNum"
            ),
            tag_file=config.get("Tags", "TagFile"),
        )
        types = [t.strip().lower() for t in config.get("Tags", "ColumnTypes").split(",")]
        types = [{"cate": "categorical", "num": "numeric"}.get(t, t) for t in types]
        num_attributes = config.getint("Tags", "NumTagsPerVec", fallback=len(types))
        limited_tag_column = config.getint(
            "BuildSSDIndex", "LimitedTagColumn", fallback=0
        )
        limited_tag_enabled = config.getboolean(
            "BuildSSDIndex", "EnableLimitedTagPosting"
        )
    except (configparser.Error, ValueError) as error:
        raise ValueError(
            f"{path}: incomplete label-coverage configuration"
        ) from error
    if (
        policy.vector_count <= 0
        or num_attributes != 2
        or types != ["categorical", "numeric"]
        or limited_tag_column != 0
        or not limited_tag_enabled
    ):
        raise ValueError(
            f"{path}: expected enabled one-tag plus one-numeric "
            "limited-tag configuration"
        )
    coverage_boundary_count(policy.vector_count, policy)
    return policy

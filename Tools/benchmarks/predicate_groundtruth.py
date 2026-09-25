"""Validated column-aware predicates and exact, bounded top-k primitives."""

import json
import operator

import numpy as np

from generate_sift1m_sparse_numeric_workloads import encode_dnf3, repeat_length_prefixed


OPERATIONS = {"eq": 0, "lt": 1, "le": 2, "gt": 3, "ge": 4}
COMPARISONS = (operator.eq, operator.lt, operator.le, operator.gt, operator.ge)
MAX_CLAUSES = 64
MAX_LITERALS = 64


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate predicate key: {key}")
        result[key] = value
    return result


def parse_predicate(text, schema):
    expression = json.loads(text, object_pairs_hook=unique_object)
    if expression is None:
        return None, []

    def compile_node(node, depth=0):
        if depth > 32:
            raise ValueError("Predicate nesting exceeds 32 levels")
        if not isinstance(node, dict) or len(node) != 1:
            raise ValueError("Each predicate node must contain exactly one operator")
        name, value = next(iter(node.items()))
        if name in ("and", "or"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"Predicate {name} requires a nonempty list")
            clauses = [[]] if name == "and" else []
            for child in value:
                terms = compile_node(child, depth + 1)
                if name == "or":
                    if len(clauses) + len(terms) > MAX_CLAUSES:
                        raise ValueError("Predicate expands beyond 64 DNF clauses")
                    clauses.extend(terms)
                else:
                    if len(clauses) * len(terms) > MAX_CLAUSES:
                        raise ValueError("Predicate expands beyond 64 DNF clauses")
                    clauses = [left + right for left in clauses for right in terms]
                    if any(len(clause) > MAX_LITERALS for clause in clauses):
                        raise ValueError("A DNF clause exceeds 64 literals")
            return clauses
        parts = name.split("_")
        if len(parts) != 2 or parts[0] not in ("categorical", "numeric"):
            raise ValueError(f"Unsupported predicate operator: {name}")
        kind, operation = parts
        if operation not in OPERATIONS or (kind == "categorical" and operation != "eq"):
            raise ValueError(f"Unsupported predicate operator: {name}")
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"{name} requires [original_column, uint32_value]")
        column, threshold = value
        if type(column) is not int or not 0 <= column < len(schema):
            raise ValueError(f"Invalid original predicate column: {column}")
        if schema[column] != kind:
            raise ValueError(f"Predicate kind disagrees with ColumnTypes at column {column}")
        if type(threshold) is not int or not 0 <= threshold <= 0xFFFFFFFF:
            raise ValueError(f"Predicate value must be uint32: {threshold}")
        return [[(int(kind == "numeric"), column, OPERATIONS[operation], threshold)]]

    return expression, compile_node(expression)


def matching_rows(clauses, attributes):
    if not clauses:
        return np.ones(len(attributes), dtype=bool)
    matched = np.zeros(len(attributes), dtype=bool)
    for clause in clauses:
        conjunction = np.ones(len(attributes), dtype=bool)
        for _, column, operation, threshold in clause:
            conjunction &= COMPARISONS[operation](attributes[:, column], threshold)
        matched |= conjunction
    return matched


def native_predicate(clauses, schema, query_count):
    if not clauses:
        return "empty", None
    # Flat native tag lists do not bind a column; only use them when unambiguous.
    if (schema == ("categorical", "numeric") and len(clauses) == 1
            and len(clauses[0]) == 1 and clauses[0][0][:3] == (0, 0, 0)):
        return "categorical", np.full((query_count, 1), clauses[0][0][3], dtype="<u4")
    return "dnf", repeat_length_prefixed(encode_dnf3(clauses), query_count)


def stable_topk(ids, distances, topk):
    if topk <= 0:
        raise ValueError("top-k must be positive")
    valid = (ids >= 0) & np.isfinite(distances)
    ids, distances = ids[valid], distances[valid]
    count = min(topk, len(ids))
    if count == 0:
        return ids, distances
    if count < len(ids):
        boundary = np.partition(distances, count - 1)[count - 1]
        closer = np.flatnonzero(distances < boundary)
        tied = np.flatnonzero(distances == boundary)
        remaining = count - len(closer)
        if len(tied) > remaining:
            tied = tied[np.argpartition(ids[tied], remaining - 1)[:remaining]]
        selected = np.concatenate((closer, tied))
    else:
        selected = np.arange(len(ids))
    selected = selected[np.lexsort((ids[selected], distances[selected]))]
    return ids[selected], distances[selected]


def knn_l2(xq, xb, topk):
    """Preserve the exhaustive helper used by archived selectivity preparation."""
    import faiss

    if topk <= 0 or xq.ndim != 2 or xb.ndim != 2 or xq.shape[1] != xb.shape[1]:
        raise ValueError("Exact kNN requires compatible matrices and positive top-k")
    positions = np.full((len(xq), topk), -1, dtype=np.int64)
    distances = np.full((len(xq), topk), np.inf, dtype=np.float32)
    if len(xb):
        count = min(topk, len(xb))
        values, indices = faiss.knn(
            np.ascontiguousarray(xq, dtype=np.float32),
            np.ascontiguousarray(xb, dtype=np.float32),
            count,
            metric=faiss.METRIC_L2,
        )
        positions[:, :count], distances[:, :count] = indices, values
    return positions, distances

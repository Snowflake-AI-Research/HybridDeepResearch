"""Standalone execution-match scoring for S2SQL tasks.

The headline entry point is :func:`compare_execution_results`. Inputs
are column-major dicts (``{"col_name": [values, ...]}``) — that's the
shape the SQLite executor returns once we transpose its row-major
output.

Behavior preserved:

- 2-digit float rounding (``NUMBER_OF_DIGITS_CONSIDERED``).
- String cells compare **case-insensitively** after strip (``"No"`` ==
  ``"no"``); JSON leaf strings use the same rule.
- JSON array cells compare by **key-agnostic primitive-value coverage**:
  the smaller object's leaf values must each match a distinct value in
  the larger object (so compact gold summaries match verbose agent JSON
  without hand-written field aliases).
- Hungarian-algorithm column matching via
  ``scipy.optimize.linear_sum_assignment``.
- Loose mode keeps the 75 % precision/recall threshold boost and the
  one-vs-many row tolerance for ``ORDER BY`` queries.
- Strict mode disables both.
- ``is_sql_ordered`` uses sqlglot and tolerates parse failures by falling
  back to a substring scan for ``order by``.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Tuple

import numpy as np
import sqlglot
from sqlglot.optimizer.scope import traverse_scope


NUMBER_OF_DIGITS_CONSIDERED = 2
S2SQL_NUMBER_OF_DIGITS_CONSIDERED = 3
MAX_NUMBER_OF_EXTRA_COLUMNS = 3
MAX_NUMBER_OF_MISSING_COLUMNS = 1
_MISSING_COLUMNS_SCORE = 0.5
_WRONG_ORDER_SCORE = 0.5
PERCENTAGE_ATOL = 0.1


def _try_parse_json_value(val: Any) -> Any | None:
    if isinstance(val, str):
        text = val.strip()
        if not text or text[0] not in "[{":
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(val, (list, dict)):
        return val
    return None


def _normalize_primitive(val: Any, *, float_digits: int = NUMBER_OF_DIGITS_CONSIDERED) -> Any:
    if isinstance(val, float):
        return round(val, float_digits)
    if isinstance(val, str):
        # Case-insensitive cell match: "No" and "no" count as equal.
        return val.strip().casefold()
    return val


def _values_equal(
    a: Any,
    b: Any,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    a = _normalize_primitive(a, float_digits=float_digits)
    b = _normalize_primitive(b, float_digits=float_digits)
    if isinstance(a, float) and isinstance(b, (int, float)):
        return bool(np.isclose(float(a), float(b), rtol=1e-5, atol=1e-8, equal_nan=True))
    if isinstance(b, float) and isinstance(a, (int, float)):
        return bool(np.isclose(float(a), float(b), rtol=1e-5, atol=1e-8, equal_nan=True))
    return a == b


def _primitive_values(
    obj: Any,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> List[Any]:
    """Collect leaf primitives from a parsed JSON structure."""
    if isinstance(obj, dict):
        out: List[Any] = []
        for v in obj.values():
            out.extend(_primitive_values(v, float_digits=float_digits))
        return out
    if isinstance(obj, list):
        out: List[Any] = []
        for item in obj:
            out.extend(_primitive_values(item, float_digits=float_digits))
        return out
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return [_normalize_primitive(obj, float_digits=float_digits)]
    return [obj]


def _primitive_values_cover(
    smaller: List[Any],
    larger: List[Any],
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    """Return True when every value in *smaller* matches a distinct value in *larger*."""
    if not smaller:
        return True
    used = [False] * len(larger)
    for sv in smaller:
        matched = False
        for i, lv in enumerate(larger):
            if used[i]:
                continue
            if _values_equal(sv, lv, float_digits=float_digits):
                used[i] = True
                matched = True
                break
        if not matched:
            return False
    return True


def _json_objects_compatible(
    left: Any,
    right: Any,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    """Key-agnostic JSON object/array compatibility via value coverage."""
    if isinstance(left, dict) and isinstance(right, dict):
        v_left = _primitive_values(left, float_digits=float_digits)
        v_right = _primitive_values(right, float_digits=float_digits)
        if len(v_left) <= len(v_right):
            return _primitive_values_cover(v_left, v_right, float_digits=float_digits)
        return _primitive_values_cover(v_right, v_left, float_digits=float_digits)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(
            _json_objects_compatible(a, b, float_digits=float_digits)
            for a, b in zip(left, right)
        )
    return _values_equal(left, right, float_digits=float_digits)


def _json_parsed_equal(
    left: Any,
    right: Any,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    if isinstance(left, dict) and not isinstance(right, dict):
        right = [right]
        left = [left]
    if isinstance(right, dict) and not isinstance(left, dict):
        left = [left]
        right = [right]
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(
            _json_objects_compatible(a, b, float_digits=float_digits)
            for a, b in zip(left, right)
        )
    return _json_objects_compatible(left, right, float_digits=float_digits)


def _cells_equal(
    left: Any,
    right: Any,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    left_json = _try_parse_json_value(left)
    right_json = _try_parse_json_value(right)
    if left_json is not None and right_json is not None:
        return _json_parsed_equal(left_json, right_json, float_digits=float_digits)
    return _values_equal(left, right, float_digits=float_digits)


def _columns_equal(
    col_a: List[Any],
    col_b: List[Any],
    *,
    order_matters: bool,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    if len(col_a) != len(col_b):
        return False
    if order_matters:
        return all(
            _cells_equal(a, b, float_digits=float_digits) for a, b in zip(col_a, col_b)
        )
    unmatched = list(col_b)
    for a in col_a:
        for i, b in enumerate(unmatched):
            if _cells_equal(a, b, float_digits=float_digits):
                unmatched.pop(i)
                break
        else:
            return False
    return True


def _round_floats(
    denotation: Dict[str, List[Any]],
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> None:
    """Round floats in place to *float_digits* decimal places."""
    for column_values in denotation.values():
        for ix, value in enumerate(column_values):
            if isinstance(value, float):
                column_values[ix] = round(value, float_digits)


def _sequences_equal_ignore_order(seq1: List[Any], seq2: List[Any]) -> bool:
    unmatched = deepcopy(seq2)
    for el in seq1:
        try:
            unmatched.remove(el)
        except ValueError:
            return False
    return not unmatched


def _rows_for_dataframe(df: Dict[str, List[Any]]) -> int:
    vals = list(df.values())
    return len(vals[0]) if vals else 0


def _dataframe_similarity(
    pred: Dict[str, List[Any]],
    gold: Dict[str, List[Any]],
    order_matters: bool,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> float:
    """Hungarian-algorithm column match; return [0, 1] similarity."""
    from scipy.optimize import linear_sum_assignment

    if not pred:
        return 0.0
    extra = len(pred) - len(gold)
    if extra > MAX_NUMBER_OF_EXTRA_COLUMNS or -extra > MAX_NUMBER_OF_MISSING_COLUMNS:
        return 0.0

    if order_matters:
        def metric(a: List, b: List) -> float:
            return (
                0.0
                if _columns_equal(a, b, order_matters=True, float_digits=float_digits)
                else 1.0
            )
    else:
        def metric(a: List, b: List) -> float:
            return (
                0.0
                if _columns_equal(a, b, order_matters=False, float_digits=float_digits)
                else 1.0
            )

    pred_values = list(pred.values())
    gold_values = list(gold.values())
    distances = np.empty((len(pred_values), len(gold_values)), dtype=np.double)
    for i, pv in enumerate(pred_values):
        for j, gv in enumerate(gold_values):
            distances[i, j] = metric(pv, gv)
    row_ind, col_ind = linear_sum_assignment(cost_matrix=distances)
    min_cost = round(sum(distances[i, j] for i, j in zip(row_ind, col_ind)))

    if extra < 0 and min_cost == 0:
        return _MISSING_COLUMNS_SCORE
    if min_cost == 0:
        return 1.0
    if order_matters and _rows_for_dataframe(pred) == _rows_for_dataframe(gold):
        return _dataframe_similarity(pred, gold, False, float_digits=float_digits) * _WRONG_ORDER_SCORE
    return 0.0


def _check_one_vs_many(
    top: Dict[str, List[Any]],
    all_rows: Dict[str, List[Any]],
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> bool:
    """Match ``top`` (1 row) against the first row of ``all_rows`` (>1 rows)."""
    if not (_rows_for_dataframe(top) == 1 and _rows_for_dataframe(all_rows) > 1):
        return False
    truncated = {col: [vals[0]] for col, vals in all_rows.items()}
    return _dataframe_similarity(top, truncated, order_matters=True, float_digits=float_digits) > 0.5


def _compute_eval_score(
    pred: Dict[str, List[Any]],
    gold: Dict[str, List[Any]],
    order_matters: bool,
    max_extra: int,
    *,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> float:
    if order_matters:
        if (
            _check_one_vs_many(pred, gold, float_digits=float_digits)
            or _check_one_vs_many(gold, pred, float_digits=float_digits)
        ):
            return 1.0
    if not pred and not gold:
        return 1.0
    if len(pred) + MAX_NUMBER_OF_MISSING_COLUMNS < len(gold):
        return 0.0
    if len(pred) > len(gold) + max_extra:
        return 0.0
    return _dataframe_similarity(pred, gold, order_matters, float_digits=float_digits)


def _get_overlap_columns(
    denotation_pred: Dict[str, List[Any]],
    denotation_gold: Dict[str, List[Any]],
    *,
    ignore_extra_columns_for_recall: bool = False,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> Tuple[int, int, float, float]:
    """Per-column matching via ``pandas.testing.assert_series_equal`` (loose).

    Returns ``(exact, percent, precision, recall)`` where (matching the
    upstream "swapped" convention):

    - precision = matches / |gold cols|
    - recall    = matches / |pred cols|, or matches / |gold cols| when
      *ignore_extra_columns_for_recall* is set (extra agent columns no
      longer penalize recall — used by the s2sql judge).
    """
    import pandas as pd
    from pandas.testing import assert_series_equal

    df_gold = pd.DataFrame(denotation_gold)
    df_pred = pd.DataFrame(denotation_pred)
    if df_pred.empty or len(df_pred) != len(df_gold):
        return 0, 0, 0.0, 0.0

    df_gold_temp = df_gold.copy(deep=True)
    n_exact = 0
    n_percent = 0
    for col_pred_name in df_pred.columns:
        for col_gold_name in list(df_gold_temp.columns):
            col_pred = df_pred[col_pred_name].sort_values().reset_index(drop=True)
            col_gold = df_gold_temp[col_gold_name].sort_values().reset_index(drop=True)
            if col_pred.dtype == "object":
                col_pred = col_pred.where(col_pred.notna(), None)
            if col_gold.dtype == "object":
                col_gold = col_gold.where(col_gold.notna(), None)
            if pd.api.types.is_datetime64_any_dtype(col_pred) or pd.api.types.is_datetime64_any_dtype(col_gold):
                try:
                    col_pred = col_pred.astype(str)
                    col_gold = col_gold.astype(str)
                except Exception:
                    pass
            try:
                assert_series_equal(col_pred, col_gold, check_dtype=False, check_names=False)
                n_exact += 1
                df_gold_temp = df_gold_temp.drop(columns=[col_gold_name])
                break
            except (AssertionError, TypeError, AttributeError):
                if _columns_equal(
                    col_pred.tolist(),
                    col_gold.tolist(),
                    order_matters=False,
                    float_digits=float_digits,
                ):
                    n_exact += 1
                    df_gold_temp = df_gold_temp.drop(columns=[col_gold_name])
                    break
                col_pred_num = pd.to_numeric(col_pred, errors="coerce")
                col_gold_num = pd.to_numeric(col_gold, errors="coerce")
                if col_pred_num.isnull().all() or col_gold_num.isnull().all():
                    continue
                if np.isclose(col_pred_num, col_gold_num, rtol=1e-5, atol=1e-8, equal_nan=True).all():
                    n_exact += 1
                    df_gold_temp = df_gold_temp.drop(columns=[col_gold_name])
                    break
                if (
                    (abs(col_pred_num.dropna()) <= 100).all()
                    and (abs(col_gold_num.dropna()) <= 100).all()
                    and (
                        np.isclose(col_pred_num, col_gold_num * 100, atol=PERCENTAGE_ATOL, equal_nan=True).all()
                        or np.isclose(col_pred_num * 100, col_gold_num, atol=PERCENTAGE_ATOL, equal_nan=True).all()
                    )
                ):
                    n_percent += 1
                    df_gold_temp = df_gold_temp.drop(columns=[col_gold_name])
                    break
    total = n_exact + n_percent
    precision = total / len(df_gold.columns) if len(df_gold.columns) > 0 else 0.0
    recall_denom = (
        len(df_gold.columns)
        if ignore_extra_columns_for_recall
        else len(df_pred.columns)
    )
    recall = total / recall_denom if recall_denom > 0 else 0.0
    return n_exact, n_percent, precision, recall


def is_sql_ordered(sql: str) -> bool:
    """Return ``True`` if *sql*'s outermost SELECT carries an ``ORDER BY``."""
    if not sql:
        return False
    try:
        scopes = traverse_scope(sqlglot.parse_one(sql))
        if not scopes:
            return "order by" in sql.lower()
        for order_by in scopes[-1].find_all(sqlglot.exp.Order):
            if isinstance(order_by.parent, sqlglot.exp.Select):
                return True
    except Exception:
        return "order by" in sql.lower()
    return False


def compare_execution_results(
    pred_result: Dict[str, List[Any]],
    gold_result: Dict[str, List[Any]],
    *,
    order_matters: bool = False,
    max_number_of_extra_columns: int = MAX_NUMBER_OF_EXTRA_COLUMNS,
    strict: bool = False,
    ignore_extra_columns_for_recall: bool = False,
    float_digits: int = NUMBER_OF_DIGITS_CONSIDERED,
) -> Tuple[float, float, float]:
    """Return ``(eval_score_v2, column_precision, column_recall)``.

    ``eval_score_v2`` mirrors the ``aggregate_eval_results.py`` formula::

        GREATEST(
            eval_score::int,
            CASE WHEN (col_p>=0.75 AND col_r>=0.75) OR col_p>=1
                 THEN 1 ELSE 0 END
        )

    Strict mode (``strict=True``) disables both the one-vs-many row
    tolerance and the precision/recall threshold boost — useful when a
    downstream metric needs the raw similarity.

    When *ignore_extra_columns_for_recall* is ``True``, recall is
    ``matches / |gold cols|`` instead of ``matches / |pred cols|`` so
    bonus agent columns (e.g. ``seshkey``) do not block the 75 %
    threshold boost.

    *float_digits* controls how many decimal places floats are rounded
    to before comparison (default 2; s2sql uses 3).
    """
    pred_copy = deepcopy(pred_result)
    gold_copy = deepcopy(gold_result)
    _round_floats(pred_copy, float_digits=float_digits)
    _round_floats(gold_copy, float_digits=float_digits)

    if not pred_copy and not gold_copy:
        return 1.0, 1.0, 1.0
    if not pred_copy or not gold_copy:
        return 0.0, 0.0, 0.0

    pred_rows = _rows_for_dataframe(pred_copy)
    gold_rows = _rows_for_dataframe(gold_copy)
    if pred_rows != gold_rows:
        if (
            not strict
            and order_matters
            and (
                _check_one_vs_many(pred_copy, gold_copy, float_digits=float_digits)
                or _check_one_vs_many(gold_copy, pred_copy, float_digits=float_digits)
            )
        ):
            return 1.0, 1.0, 1.0
        return 0.0, 0.0, 0.0

    eval_score = _compute_eval_score(
        pred_copy,
        gold_copy,
        order_matters,
        max_number_of_extra_columns,
        float_digits=float_digits,
    )
    _, _, col_p, col_r = _get_overlap_columns(
        pred_copy,
        gold_copy,
        ignore_extra_columns_for_recall=ignore_extra_columns_for_recall,
        float_digits=float_digits,
    )

    threshold_met = bool(
        col_p
        and col_r
        and ((col_p >= 0.75 and col_r >= 0.75) or col_p >= 1.0)
    )
    if strict:
        eval_score_v2 = eval_score
    elif threshold_met:
        eval_score_v2 = 1.0
    else:
        eval_score_v2 = eval_score
    return eval_score_v2, col_p, col_r

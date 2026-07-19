"""Execution-result matching for the golden-set eval.

The eval scores by *denotation*, not SQL string comparison (PRD testing
decision): two queries match when running them yields the same answer. Real
NL to SQL output varies wildly in surface form — column aliases, column
order, row order, and extra context columns the model volunteers — so a
naive row-tuple comparison would reject correct answers.

`result_matches` therefore treats the expected result as the ground truth and
asks: does the prediction contain the expected answer? It searches for an
ordered selection of the prediction's columns whose projected rows equal the
expected rows as a multiset (row-order-insensitive). This tolerates column
renaming, column reordering, and extra prediction columns, while preserving
the within-row association between values so a query that pairs the right
numbers with the wrong groups still fails.
"""

from itertools import permutations

# The golden SQL rounds every rate to 3 decimals, and the golden rate
# questions ask for a percentage so the model returns the same scale; the
# translator prompt likewise instructs rounding to 3 decimals. Comparing at 3
# decimals therefore aligns the two sides without masking real differences.
_NDIGITS = 3

# Projection searches permutations of prediction columns, which is factorial.
# A prediction this wide is a raw row dump, not an aggregate answer; fall back
# to requiring an exact column count so the search stays bounded.
_MAX_PROJECTION_COLUMNS = 8


def _normalize_cell(value: object) -> object:
    """Collapse incidental representation differences before comparing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), _NDIGITS)
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_rows(rows: list[list]) -> list[tuple]:
    return [tuple(_normalize_cell(cell) for cell in row) for row in rows]


def _multiset(rows: list[tuple]) -> dict[tuple, int]:
    counts: dict[tuple, int] = {}
    for row in rows:
        counts[row] = counts.get(row, 0) + 1
    return counts


def result_matches(
    expected_rows: list[list],
    actual_rows: list[list],
) -> bool:
    """True when `actual_rows` denotes the same answer as `expected_rows`.

    Both are row lists (list of column-value lists) as returned by the guarded
    executor and the /api/explore endpoint. Column names are irrelevant;
    matching is by value under column projection.
    """
    expected = _normalize_rows(expected_rows)
    actual = _normalize_rows(actual_rows)

    expected_width = len(expected[0]) if expected else 0
    actual_width = len(actual[0]) if actual else 0

    if not expected:
        # An empty expected result matches only an empty prediction.
        return not actual
    if expected_width > actual_width:
        return False
    if len(expected) != len(actual):
        # Multiset equality requires equal row counts; a prediction that
        # returned a different number of rows cannot denote the same answer.
        return False

    target = _multiset(expected)

    if actual_width > _MAX_PROJECTION_COLUMNS:
        return actual_width == expected_width and _multiset(actual) == target

    for selection in permutations(range(actual_width), expected_width):
        projected = _multiset([tuple(row[i] for i in selection) for row in actual])
        if projected == target:
            return True
    return False

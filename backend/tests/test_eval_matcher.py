"""The eval's execution-result matcher: denotation match tolerant of column
aliasing, ordering, and extra columns, but not of wrong value associations."""

from fsm_assistant.evals.matcher import result_matches


def test_identical_scalar_matches():
    assert result_matches([[0.174]], [[0.174]])


def test_column_alias_and_extra_columns_are_ignored():
    # Question: overall fraud rate. Expected is the single rate; the model
    # volunteered labeled/fraud counts alongside it.
    assert result_matches([[0.174]], [[3000, 5, 0.174]])


def test_scalar_off_by_more_than_tolerance_does_not_match():
    assert not result_matches([[0.174]], [[0.199]])


def test_rounding_within_three_decimals_matches():
    assert result_matches([[0.1740001]], [[0.174]])


def test_row_order_does_not_matter():
    expected = [["Online Transaction", 100], ["Swipe Transaction", 50]]
    actual = [["Swipe Transaction", 50], ["Online Transaction", 100]]
    assert result_matches(expected, actual)


def test_column_order_does_not_matter():
    expected = [["Online Transaction", 100]]
    actual = [[100, "Online Transaction"]]
    assert result_matches(expected, actual)


def test_extra_column_on_grouped_result_is_projected_away():
    # Expected: rate per type. Prediction added a labeled-count column.
    expected = [["Online Transaction", 0.5], ["Swipe Transaction", 0.1]]
    actual = [["Online Transaction", 2000, 0.5], ["Swipe Transaction", 900, 0.1]]
    assert result_matches(expected, actual)


def test_wrong_group_to_value_association_fails():
    # Same numbers, but paired with the wrong groups: must not match.
    expected = [["Online Transaction", 0.5], ["Swipe Transaction", 0.1]]
    actual = [["Online Transaction", 0.1], ["Swipe Transaction", 0.5]]
    assert not result_matches(expected, actual)


def test_different_row_count_fails():
    assert not result_matches([[1], [2], [3]], [[1], [2]])


def test_empty_expected_matches_only_empty_actual():
    assert result_matches([], [])
    assert not result_matches([], [[1]])


def test_prediction_narrower_than_expected_fails():
    assert not result_matches([["a", "b"]], [["a"]])


def test_wide_prediction_falls_back_to_exact_width():
    expected = [[1, 2]]
    wide = [list(range(9))]
    assert not result_matches(expected, wide)

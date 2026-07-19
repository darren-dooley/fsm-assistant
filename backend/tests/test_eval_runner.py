"""The eval runner scores the golden set through the same HTTP seam as the
product. These tests drive it with the scripted fake LLM and the fixture
database, so the scoring logic is verified deterministically; the real-LLM run
stays on demand via `fsm-eval`."""

from conftest import CUTOFF, FakeLLM, build_fixture_data_db, chat_reply, decline_reply

from fsm_assistant.config import Settings
from fsm_assistant.evals.golden_set import GoldenCase
from fsm_assistant.evals.runner import CaseResult, EvalReport, aggregate_reports, run_eval


def _settings(tmp_path) -> Settings:
    data_db = tmp_path / "data.db"
    build_fixture_data_db(data_db)
    return Settings(
        data_db_path=data_db,
        app_db_path=tmp_path / "app.db",
        cache_db_path=tmp_path / "cache.db",
        cutoff=CUTOFF,
    )


# The fixture visible slice: 4 pre-cutoff transactions, 1 labeled fraud.
_CASES = [
    GoldenCase(
        id="ok_count",
        category="aggregate",
        question="How many transactions are there?",
        sql="SELECT COUNT(*) AS n FROM transactions",
    ),
    GoldenCase(
        id="wrong_answer",
        category="aggregate",
        question="How many are fraud?",
        sql="SELECT SUM(is_fraud) AS n FROM fraud_labels",
    ),
    GoldenCase(
        id="refuses",
        category="unanswerable",
        question="Which email addresses are fraudulent?",
    ),
]


def _fake_run(tmp_path):
    fake = FakeLLM(
        [
            # ok_count: correct SQL proposed, then the run's combined turn
            chat_reply("Counting.", "SELECT COUNT(*) AS n FROM transactions"),
            decline_reply("There are 4 transactions.", "Just a count."),
            # wrong_answer: proposes SQL returning the wrong number
            chat_reply("Here.", "SELECT 999 AS n"),
            decline_reply("It is 999.", "Just a count."),
            # refuses: replies without proposing SQL
            chat_reply("The data has no email addresses."),
        ]
    )
    return run_eval(_CASES, _settings(tmp_path), fake)


def test_correct_answer_matches_by_execution_result(tmp_path):
    report = _fake_run(tmp_path)
    ok = next(r for r in report.results if r.id == "ok_count")
    assert ok.status == "ok"
    assert ok.correct is True
    assert ok.note == "matched"


def test_wrong_result_is_scored_as_miss_not_string_compared(tmp_path):
    report = _fake_run(tmp_path)
    wrong = next(r for r in report.results if r.id == "wrong_answer")
    # Executor computes the expected (1 fraud) and the fake returned 999.
    assert wrong.status == "ok"
    assert wrong.correct is False
    assert wrong.note == "result mismatch"


def test_unanswerable_scored_on_refusal_behavior(tmp_path):
    report = _fake_run(tmp_path)
    refusal = next(r for r in report.results if r.id == "refuses")
    assert refusal.status == "refusal"
    assert refusal.correct is True


def test_metrics_are_rates_not_pass_fail(tmp_path):
    metrics = _fake_run(tmp_path).metrics()
    assert metrics["cases"] == 3
    assert metrics["answerable"] == 2
    assert metrics["unanswerable"] == 1
    assert metrics["match_rate"] == 0.5  # 1 of 2 answerable correct
    assert metrics["refusal_rate_on_unanswerable"] == 1.0
    assert metrics["false_refusal_rate"] == 0.0
    assert metrics["by_category"]["aggregate"]["correct_rate"] == 0.5


def test_repair_depth_is_reported(tmp_path):
    depth = _fake_run(tmp_path).metrics()["repair_depth"]
    assert depth["max_attempts"] == 1
    assert depth["attempts_histogram"] == {"1": 3}


def test_needless_label_join_fails_case_even_when_result_matches(tmp_path):
    # The count is right (extra columns are tolerated by the matcher), but the
    # SQL drags fraud_labels into an attribute-only question — a query the FSM
    # cannot turn into a rule, and a join away from silently dropping the
    # unlabeled third. The shape lint must fail it despite the matching result.
    case = GoldenCase(
        id="attr_count",
        category="aggregate",
        question="How many transactions are there?",
        sql="SELECT COUNT(*) AS n FROM transactions",
    )
    predicted = (
        "SELECT COUNT(*) AS n,\n"
        "  (SELECT COUNT(*) FROM fraud_labels) AS labeled_context\n"
        "FROM transactions"
    )
    fake = FakeLLM(
        [chat_reply("Counting.", predicted), decline_reply("4 transactions.", "Just a count.")]
    )
    result = run_eval([case], _settings(tmp_path), fake).results[0]
    assert result.status == "ok"
    assert result.correct is False
    assert result.lints == ("needless_fraud_labels",)
    assert result.note == "right result, wrong shape: needless_fraud_labels"


def test_diluted_denominator_is_named_as_shape_defect(tmp_path):
    # Issue #14's observed failure shape: LEFT JOIN with unlabeled rows
    # coalesced to non-fraud. The result mismatches anyway (25% vs the labeled
    # 33.333%), but the note must name the shape, not just "result mismatch".
    case = GoldenCase(
        id="fraud_rate",
        category="aggregate",
        question="What is the fraud rate, as a percentage?",
        sql="SELECT ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3) AS r FROM fraud_labels",
    )
    predicted = (
        "SELECT ROUND(100.0 * SUM(COALESCE(f.is_fraud, 0)) / COUNT(*), 3) AS r\n"
        "FROM transactions t\n"
        "LEFT JOIN fraud_labels f ON f.transaction_id = t.id"
    )
    fake = FakeLLM(
        [chat_reply("Rate.", predicted), decline_reply("The rate is 25%.", "Just a rate.")]
    )
    result = run_eval([case], _settings(tmp_path), fake).results[0]
    assert result.correct is False
    assert set(result.lints) == {"left_join_fraud_labels", "coalesced_is_fraud"}
    assert result.note == (
        "result mismatch; shape: left_join_fraud_labels, coalesced_is_fraud"
    )


def test_single_line_sql_is_a_rate_not_a_failure(tmp_path):
    # Both fakes in _fake_run propose single-line SQL; the rate reports it,
    # and ok_count still passes.
    report = _fake_run(tmp_path)
    assert report.metrics()["sql_shape"]["single_line_sql_rate"] == 1.0
    ok = next(r for r in report.results if r.id == "ok_count")
    assert ok.single_line_sql is True
    assert ok.correct is True


def _result(case_id: str, correct: bool, note: str) -> CaseResult:
    return CaseResult(
        id=case_id,
        category="aggregate",
        expected="answer",
        status="ok",
        attempts=1,
        correct=correct,
        note=note,
    )


def test_aggregate_reports_exposes_per_case_flake():
    steady = EvalReport(results=[_result("a", True, "matched")])
    flaky = EvalReport(results=[_result("a", False, "result mismatch")])
    aggregate = aggregate_reports([steady, flaky])
    assert aggregate["runs"] == 2
    assert aggregate["match_rate"] == {"mean": 0.5, "min": 0.0, "max": 1.0}
    case = aggregate["per_case"][0]
    assert case["passes"] == 1
    assert case["pass_rate"] == 0.5
    assert case["failure_notes"] == ["result mismatch"]


def test_answerable_refusal_counts_as_false_refusal(tmp_path):
    case = GoldenCase(
        id="should_answer",
        category="aggregate",
        question="How many transactions?",
        sql="SELECT COUNT(*) AS n FROM transactions",
    )
    fake = FakeLLM([chat_reply("I can't answer that from this data.")])
    report = run_eval([case], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.correct is False
    assert result.status == "refusal"
    assert report.metrics()["false_refusal_rate"] == 1.0

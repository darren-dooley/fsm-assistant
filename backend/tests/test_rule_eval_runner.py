"""The rule-quality eval runner drives the combined Run turn through the
product's HTTP seam and scores the result against hand-computed backtest
counts. These tests drive it with the scripted fake LLM and the pattern
fixture database, so the scoring logic is verified deterministically; the
real-LLM run stays on demand via `fsm-eval rules`."""

from conftest import FakeLLM, decline_reply, rule_reply

from fsm_assistant.config import Settings
from fsm_assistant.evals.fixture import build_dataset_db
from fsm_assistant.evals.rule_patterns import (
    CUTOFF,
    FIXTURE_LABELS,
    FIXTURE_TRANSACTIONS,
    ExpectedCounts,
    RulePattern,
)
from fsm_assistant.evals.rule_runner import run_rule_eval


def _settings(tmp_path, **overrides) -> Settings:
    data_db = tmp_path / "data.db"
    if not data_db.exists():
        build_dataset_db(data_db, FIXTURE_TRANSACTIONS, FIXTURE_LABELS)
    return Settings(
        data_db_path=data_db,
        app_db_path=tmp_path / "app.db",
        cache_db_path=tmp_path / "cache.db",
        cutoff=CUTOFF,
        **overrides,
    )


_BAD_PIN = RulePattern(
    id="bad_pin",
    intent="Flag every transaction that failed with a Bad PIN error.",
    sql=(
        "SELECT COUNT(*) AS bad_pin_labeled, SUM(f.is_fraud) AS fraud "
        "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id "
        "WHERE t.errors = 'Bad PIN'"
    ),
    reference_clause="errors = 'Bad PIN'",
    expected=ExpectedCounts(
        flagged_total=4, flagged_labeled=4, fraud_caught=2, legit_blocked=2
    ),
)


_WHOLE_TABLE = RulePattern(
    id="whole_table_count",
    intent="How many transactions are there in total?",
    sql="SELECT COUNT(*) AS transaction_count FROM transactions",
)


def test_faithful_draft_is_scored_by_count_match(tmp_path):
    fake = FakeLLM([rule_reply("4 Bad PIN transactions, 2 fraud.", "errors = 'Bad PIN'")])
    report = run_rule_eval([_BAD_PIN], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.status == "ok"
    assert result.correct is True
    assert result.note == "metrics matched"
    assert result.clause == "errors = 'Bad PIN'"
    assert result.achieved["fraud_caught"] == 2
    assert result.achieved["score"] > 0


def test_run_turn_sees_intent_and_real_rows(tmp_path):
    # The eval drives the shipped path: the drafting prompt carries the
    # pattern's intent and the rows the evidence query actually returned.
    fake = FakeLLM([rule_reply("4 Bad PIN transactions.", "errors = 'Bad PIN'")])
    run_rule_eval([_BAD_PIN], _settings(tmp_path), fake)
    prompt = str(fake.calls[0])
    assert "Flag every transaction that failed with a Bad PIN error." in prompt
    assert "[[4, 2]]" in prompt  # 4 labeled Bad PIN rows, 2 fraud


def test_equivalent_phrasing_still_matches(tmp_path):
    # Scoring is by counts, not clause strings: LIKE lands on the same rows.
    fake = FakeLLM([rule_reply("4 rows.", "errors LIKE 'Bad PIN'")])
    report = run_rule_eval([_BAD_PIN], _settings(tmp_path), fake)
    assert report.results[0].correct is True


def test_unfaithful_clause_is_a_miss_with_named_counts(tmp_path):
    # Valid clause, wrong pattern: flags every error, not just Bad PIN.
    fake = FakeLLM([rule_reply("4 rows.", "errors IS NOT NULL")])
    report = run_rule_eval([_BAD_PIN], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.status == "ok"
    assert result.correct is False
    assert "flagged_total 7 != 4" in result.note
    assert "fraud_caught 3 != 2" in result.note


def test_structural_pattern_scored_on_decline(tmp_path):
    fake = FakeLLM([decline_reply("There are 20 transactions.", "A count names no segment.")])
    report = run_rule_eval([_WHOLE_TABLE], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.status == "decline"
    assert result.correct is True


def test_forcing_a_rule_out_of_a_whole_table_aggregate_is_a_miss(tmp_path):
    fake = FakeLLM([rule_reply("There are 20 transactions.", "amount_usd_cents > 0")])
    report = run_rule_eval([_WHOLE_TABLE], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.correct is False
    assert result.note == "expected a decline, got a rule"


def test_declining_a_segment_pattern_is_a_false_decline(tmp_path):
    # The primary regression ADR-0007 pins: a decline on a segment-naming
    # query is a failure, never an honest refusal.
    fake = FakeLLM([decline_reply("4 rows.", "These results cannot become a rule.")])
    report = run_rule_eval([_BAD_PIN], _settings(tmp_path), fake)
    result = report.results[0]
    assert result.status == "decline"
    assert result.correct is False
    assert "These results cannot become a rule." in result.note
    assert report.metrics()["false_decline_rate"] == 1.0


def test_exhausted_repair_loop_counts_as_a_false_decline(tmp_path):
    # Drafting exhaustion degrades to summary-plus-decline (rows always
    # return), so for a metric pattern it lands as a false decline whose note
    # names the failure.
    fake = FakeLLM([rule_reply("4 rows.", "nonexistent_column = 1")])
    report = run_rule_eval(
        [_BAD_PIN], _settings(tmp_path, max_translation_attempts=1), fake
    )
    result = report.results[0]
    assert result.status == "decline"
    assert result.correct is False
    assert "Couldn't draft a valid rule" in result.note
    assert report.metrics()["false_decline_rate"] == 1.0


def test_failed_evidence_query_is_an_eval_error(tmp_path):
    broken = RulePattern(
        id="broken_sql",
        intent="Flag every transaction that failed with a Bad PIN error.",
        sql="SELECT no_such_column FROM transactions",
        reference_clause="errors = 'Bad PIN'",
        expected=_BAD_PIN.expected,
    )
    report = run_rule_eval([broken], _settings(tmp_path), FakeLLM([]))
    result = report.results[0]
    assert result.status == "error"
    assert result.correct is False
    assert report.metrics()["error_rate"] == 1.0


def test_metrics_are_rates_not_pass_fail(tmp_path):
    fake = FakeLLM(
        [
            rule_reply("4 rows.", "errors = 'Bad PIN'"),  # correct
            rule_reply("7 rows.", "errors IS NOT NULL"),  # wrong counts
            decline_reply("20 transactions.", "A count names no segment."),  # honest
        ]
    )
    patterns = [
        _BAD_PIN,
        RulePattern(
            id="bad_pin_again",
            intent=_BAD_PIN.intent,
            sql=_BAD_PIN.sql,
            reference_clause="errors = 'Bad PIN'",
            expected=_BAD_PIN.expected,
        ),
        _WHOLE_TABLE,
    ]
    metrics = run_rule_eval(patterns, _settings(tmp_path), fake).metrics()
    assert metrics["patterns"] == 3
    assert metrics["metric_patterns"] == 2
    assert metrics["decline_patterns"] == 1
    assert metrics["match_rate"] == 0.5
    assert metrics["decline_rate_on_structural"] == 1.0
    assert metrics["false_decline_rate"] == 0.0
    assert metrics["error_rate"] == 0.0
    assert metrics["draft_depth"]["attempts_histogram"] == {"1": 3}


def test_false_decline_is_the_report_headline(tmp_path):
    # The rendered report leads with the false-decline rate: a decline on a
    # segment query is the primary regression the eval exists to catch.
    from fsm_assistant.evals.cli import render_rules

    fake = FakeLLM([rule_reply("4 rows.", "errors = 'Bad PIN'")])
    rendered = render_rules(run_rule_eval([_BAD_PIN], _settings(tmp_path), fake))
    assert "False decline rate" in rendered
    assert rendered.index("False decline rate") < rendered.index("Metrics match rate")


def test_report_serializes_clause_and_achieved_counts(tmp_path):
    fake = FakeLLM([rule_reply("4 rows.", "errors = 'Bad PIN'")])
    payload = run_rule_eval([_BAD_PIN], _settings(tmp_path), fake).to_dict()
    case = payload["patterns"][0]
    assert case["clause"] == "errors = 'Bad PIN'"
    assert case["achieved"]["flagged_total"] == 4
    assert payload["metrics"]["match_rate"] == 1.0

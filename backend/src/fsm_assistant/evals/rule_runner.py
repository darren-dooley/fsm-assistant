"""Drives the rule patterns through the product's HTTP seam and scores them.

The eval builds the same FastAPI app the product runs (`create_app`) and
drives it in-process with `TestClient` — the exact seam the test suite uses —
but wired to the real LLM, so it measures the shipped drafting path. It
mirrors the FSM's path (ADR-0006, amended by ADR-0007): the run endpoint
executes the pattern's evidence query and the combined turn translates the
segment it names into the Candidate Rule clause (declining only for a
structural reason — a whole-table aggregate or a label-defined segment), and
the backtest endpoint evaluates that clause deterministically, the same
click-Backtest flow the workbench follows.

Scoring is verifiable arithmetic, never clause string comparison: a metric
pattern is correct when the drafted clause's backtest reproduces the
hand-computed counts on the pattern fixture database (any faithful phrasing
lands on the same rows), and a structural-decline pattern is correct when
the turn declines. The headline failure is the false decline: a decline on a
segment-naming query vetoes the FSM's hypothesis before the backtest can
test it (ADR-0007's primary regression).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from fastapi.testclient import TestClient

from ..api import create_app
from ..config import Settings
from ..llm import LLMClient
from .rule_patterns import ExpectedCounts, RulePattern

# The integer counts that pin a backtest result; ratios and the Score follow
# from them deterministically, so exact count equality is the whole check.
_COUNT_FIELDS = ("flagged_total", "flagged_labeled", "fraud_caught", "legit_blocked")


@dataclass(frozen=True)
class RuleCaseResult:
    id: str
    expected: Literal["metrics", "decline"]
    status: Literal["ok", "decline", "error"]  # ok means a rule was drafted
    attempts: int
    correct: bool
    note: str
    clause: str = ""
    achieved: dict | None = None  # the drafted clause's backtest, when it ran


@dataclass
class RuleEvalReport:
    results: list[RuleCaseResult] = field(default_factory=list)

    def metrics(self) -> dict:
        metric = [r for r in self.results if r.expected == "metrics"]
        structural = [r for r in self.results if r.expected == "decline"]

        def rate(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 3) if denominator else None

        attempts = [r.attempts for r in self.results if r.attempts > 0]
        histogram: dict[int, int] = {}
        for a in attempts:
            histogram[a] = histogram.get(a, 0) + 1

        return {
            "patterns": len(self.results),
            "metric_patterns": len(metric),
            "decline_patterns": len(structural),
            # The headline: a decline on a segment-naming pattern is a veto
            # over the FSM's hypothesis, the regression ADR-0007 exists to pin.
            "false_decline_rate": rate(
                len([r for r in metric if r.status == "decline"]), len(metric)
            ),
            "match_rate": rate(len([r for r in metric if r.correct]), len(metric)),
            "decline_rate_on_structural": rate(
                len([r for r in structural if r.correct]), len(structural)
            ),
            "error_rate": rate(len([r for r in metric if r.status == "error"]), len(metric)),
            "draft_depth": {
                "mean_attempts": round(sum(attempts) / len(attempts), 2) if attempts else None,
                "max_attempts": max(attempts) if attempts else None,
                "attempts_histogram": {str(k): histogram[k] for k in sorted(histogram)},
            },
        }

    def to_dict(self) -> dict:
        return {
            "metrics": self.metrics(),
            "patterns": [
                {
                    "id": r.id,
                    "expected": r.expected,
                    "status": r.status,
                    "attempts": r.attempts,
                    "correct": r.correct,
                    "note": r.note,
                    "clause": r.clause,
                    "achieved": r.achieved,
                }
                for r in self.results
            ],
        }


def _count_mismatches(expected: ExpectedCounts, achieved: dict) -> list[str]:
    return [
        f"{name} {achieved[name]} != {getattr(expected, name)}"
        for name in _COUNT_FIELDS
        if achieved[name] != getattr(expected, name)
    ]


def _score_pattern(pattern: RulePattern, client: TestClient) -> RuleCaseResult:
    expected = "decline" if pattern.expects_decline else "metrics"
    body = client.post(
        "/api/explore/run",
        json={
            "sql": pattern.sql,
            "history": [{"role": "user", "content": pattern.intent}],
        },
    ).json()
    attempts = int(body.get("attempts", 0))

    if body.get("status") != "ok":
        # The evidence query itself failed — an eval bug, not a model miss.
        return RuleCaseResult(
            id=pattern.id,
            expected=expected,
            status="error",
            attempts=attempts,
            correct=False,
            note=f"run failed: {body.get('message', '')}".strip(),
        )

    rule = body.get("rule")
    status = "ok" if rule else "decline"

    if pattern.expects_decline:
        correct = rule is None
        note = "correctly declined" if correct else "expected a decline, got a rule"
        return RuleCaseResult(
            id=pattern.id,
            expected=expected,
            status=status,
            attempts=attempts,
            correct=correct,
            note=note,
            clause=rule["clause"] if rule else "",
        )

    if rule is None:
        # The false decline: the turn refused a segment-naming query.
        return RuleCaseResult(
            id=pattern.id,
            expected=expected,
            status=status,
            attempts=attempts,
            correct=False,
            note=f"declined a segment query: {body.get('decline_reason', '')}".strip(),
        )

    # Mirror the FSM clicking Backtest on the drafted clause. The run turn
    # already validated it, so a backtest failure is reported as an eval
    # error rather than silently skipped.
    clause = rule["clause"]
    run = client.post("/api/rules/backtest", json={"clause": clause}).json()
    if run.get("status") != "ok":
        return RuleCaseResult(
            id=pattern.id,
            expected=expected,
            status="error",
            attempts=attempts,
            correct=False,
            note=f"backtest failed: {run.get('message', '')}".strip(),
            clause=clause,
        )

    achieved = run["backtest"]
    expected_counts = pattern.expected
    assert expected_counts is not None  # metric pattern: expects_decline was False
    mismatches = _count_mismatches(expected_counts, achieved)
    return RuleCaseResult(
        id=pattern.id,
        expected=expected,
        status="ok",
        attempts=attempts,
        correct=not mismatches,
        note="metrics matched" if not mismatches else "; ".join(mismatches),
        clause=clause,
        achieved=achieved,
    )


def run_rule_eval(
    patterns: list[RulePattern],
    settings: Settings,
    llm_client: LLMClient,
    on_result: Callable[[RuleCaseResult], None] | None = None,
) -> RuleEvalReport:
    """Score `patterns` end to end. `llm_client` is the real LLM in production
    runs and a scripted fake in the deterministic runner tests."""
    app = create_app(settings, llm_client)
    report = RuleEvalReport()
    with TestClient(app) as client:
        for pattern in patterns:
            result = _score_pattern(pattern, client)
            report.results.append(result)
            if on_result is not None:
                on_result(result)
    return report

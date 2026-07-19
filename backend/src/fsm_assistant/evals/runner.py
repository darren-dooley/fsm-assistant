"""Drives the golden set through the product's HTTP seam and scores it.

The eval builds the same FastAPI app the product runs (`create_app`) and drives
it in-process with `TestClient` — the exact seam the test suite uses — but wired
to the real LLM, so it measures the shipped translation path. It mirrors the
user's path exactly: the chat endpoint proposes SQL, and when it does, the run
endpoint executes it — the same click-Run flow the FSM follows. Expected
answers are computed by executing each case's known-good SQL through the same
guarded executor the app uses, so the eval and the product see identical data
under the same cutoff and guardrails.

Scoring is by execution-result match, never SQL string comparison: an
answerable case is correct when the chat proposes SQL whose run result denotes
the golden rows (see `matcher.result_matches`); an unanswerable case is correct
when the chat replies without proposing SQL (a "refusal" in the metrics). The
only exception is the narrow shape lints (see `lints`): a known-bad SQL form —
a diluted fraud-rate denominator or a needless `fraud_labels` reference — fails
the case even when the numbers happen to match.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from ..api import create_app
from ..config import Settings
from ..guarded import GuardedExecutor
from ..llm import LLMClient
from .golden_set import GoldenCase
from .lints import is_single_line, lint_sql
from .matcher import result_matches


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    expected: str  # "answer" or "refusal"
    status: str  # ok (SQL proposed) / refusal (no SQL) / error
    attempts: int
    correct: bool
    note: str
    # Shape lints on the proposed SQL (see lints.py); any lint fails the case
    # even when the execution result matches.
    lints: tuple[str, ...] = ()
    # Readability signal only: reported as a rate, never fails a case.
    single_line_sql: bool = False


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    def metrics(self) -> dict:
        answerable = [r for r in self.results if r.expected == "answer"]
        unanswerable = [r for r in self.results if r.expected == "refusal"]

        def rate(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 3) if denominator else None

        def summarize(members: list[CaseResult]) -> dict:
            correct = sum(1 for r in members if r.correct)
            return {"n": len(members), "correct": correct, "correct_rate": rate(correct, len(members))}

        per_category = {
            cat: summarize([r for r in self.results if r.category == cat])
            for cat in sorted({r.category for r in self.results})
        }

        attempts = [r.attempts for r in self.results if r.attempts > 0]
        histogram: dict[int, int] = {}
        for a in attempts:
            histogram[a] = histogram.get(a, 0) + 1

        with_sql = [r for r in self.results if r.status == "ok"]
        lint_counts: dict[str, int] = {}
        for r in with_sql:
            for lint in r.lints:
                lint_counts[lint] = lint_counts.get(lint, 0) + 1

        return {
            "cases": len(self.results),
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
            "match_rate": rate(len([r for r in answerable if r.correct]), len(answerable)),
            "refusal_rate_on_unanswerable": rate(
                len([r for r in unanswerable if r.correct]), len(unanswerable)
            ),
            "false_refusal_rate": rate(
                len([r for r in answerable if r.status == "refusal"]), len(answerable)
            ),
            "error_rate": rate(
                len([r for r in answerable if r.status == "error"]), len(answerable)
            ),
            "repair_depth": {
                "mean_attempts": round(sum(attempts) / len(attempts), 2) if attempts else None,
                "max_attempts": max(attempts) if attempts else None,
                "attempts_histogram": {str(k): histogram[k] for k in sorted(histogram)},
            },
            "sql_shape": {
                "violation_rate": rate(len([r for r in with_sql if r.lints]), len(with_sql)),
                "violations": {k: lint_counts[k] for k in sorted(lint_counts)},
                "single_line_sql_rate": rate(
                    len([r for r in with_sql if r.single_line_sql]), len(with_sql)
                ),
            },
            "by_category": per_category,
        }

    def to_dict(self) -> dict:
        return {
            "metrics": self.metrics(),
            "cases": [
                {
                    "id": r.id,
                    "category": r.category,
                    "expected": r.expected,
                    "status": r.status,
                    "attempts": r.attempts,
                    "correct": r.correct,
                    "note": r.note,
                    "lints": list(r.lints),
                    "single_line_sql": r.single_line_sql,
                }
                for r in self.results
            ],
        }


def aggregate_reports(reports: list[EvalReport]) -> dict:
    """Cross-run aggregate for flake measurement (issue #14).

    A single run cannot tell a solid pass from a lucky one, and the observed
    failure is exactly a flake: identical questions producing correct SQL in
    one session and a diluted denominator in another. Running the same set N
    times and reporting per-case pass rates makes the flake visible and gives
    prompt changes a number to move.
    """

    def rate(numerator: float, denominator: int) -> float | None:
        return round(numerator / denominator, 3) if denominator else None

    by_case: dict[str, list[CaseResult]] = {}
    order: list[str] = []
    for report in reports:
        for r in report.results:
            if r.id not in by_case:
                by_case[r.id] = []
                order.append(r.id)
            by_case[r.id].append(r)

    per_case = []
    for case_id in order:
        results = by_case[case_id]
        passes = sum(1 for r in results if r.correct)
        per_case.append(
            {
                "id": case_id,
                "category": results[0].category,
                "runs": len(results),
                "passes": passes,
                "pass_rate": rate(passes, len(results)),
                "failure_notes": sorted({r.note for r in results if not r.correct}),
            }
        )

    def spread(values: list[float | None]) -> dict:
        present = [v for v in values if v is not None]
        if not present:
            return {"mean": None, "min": None, "max": None}
        return {
            "mean": round(sum(present) / len(present), 3),
            "min": min(present),
            "max": max(present),
        }

    metrics = [r.metrics() for r in reports]
    return {
        "runs": len(reports),
        "match_rate": spread([m["match_rate"] for m in metrics]),
        "sql_shape_violation_rate": spread(
            [m["sql_shape"]["violation_rate"] for m in metrics]
        ),
        "single_line_sql_rate": spread(
            [m["sql_shape"]["single_line_sql_rate"] for m in metrics]
        ),
        "per_case": per_case,
    }


def _history_messages(case: GoldenCase) -> list[dict]:
    """A case's prior exchanges, replayed in the chat's message shape."""
    messages: list[dict] = []
    for turn in case.history:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer, "sql": turn.sql})
    return messages


def _score_case(
    case: GoldenCase, client: TestClient, executor: GuardedExecutor
) -> CaseResult:
    history = _history_messages(case)
    body = client.post(
        "/api/explore", json={"message": case.question, "history": history}
    ).json()
    attempts = int(body.get("attempts", 0))
    sql = body.get("sql")
    # Chat outcomes fold into the report's status vocabulary: "ok" means SQL
    # was proposed, "refusal" means the assistant replied without any.
    if body.get("status") != "ok":
        status = "error"
    elif sql:
        status = "ok"
    else:
        status = "refusal"

    lints: tuple[str, ...] = ()
    single_line = False
    if case.expects_refusal:
        correct = status == "refusal"
        expected, note = "refusal", (
            "correctly declined" if correct else f"expected no SQL, got {status}"
        )
    elif status == "error":
        correct = False
        expected, note = "answer", f"error: {body.get('message', '')}".strip()
    elif status == "refusal":
        correct = False
        expected, note = "answer", "no SQL proposed"
    else:
        lints = tuple(lint_sql(case.sql, sql))  # type: ignore[arg-type]
        single_line = is_single_line(sql)
        # Mirror the user clicking Run on the proposed query.
        run = client.post(
            "/api/explore/run",
            json={
                "sql": sql,
                "history": history + [{"role": "user", "content": case.question}],
            },
        ).json()
        shape = f"shape: {', '.join(lints)}" if lints else ""
        if run.get("status") != "ok":
            correct = False
            expected, note = "answer", f"run failed: {run.get('message', '')}".strip()
        else:
            expected_rows = executor.execute(case.sql).rows  # type: ignore[arg-type]
            matched = result_matches(expected_rows, run.get("rows", []))
            # A shape lint fails the case even when the numbers match: the
            # matching result came from SQL the FSM should never be handed.
            correct = matched and not lints
            expected = "answer"
            if not matched:
                note = "; ".join(filter(None, ["result mismatch", shape]))
            elif lints:
                note = f"right result, wrong {shape}"
            else:
                note = "matched"

    return CaseResult(
        id=case.id,
        category=case.category,
        expected=expected,
        status=status,
        attempts=attempts,
        correct=correct,
        note=note,
        lints=lints,
        single_line_sql=single_line,
    )


def run_eval(
    cases: list[GoldenCase],
    settings: Settings,
    llm_client: LLMClient,
    on_result: Callable[[CaseResult], None] | None = None,
) -> EvalReport:
    """Score `cases` end to end. `llm_client` is the real LLM in production
    runs and a scripted fake in the deterministic runner tests."""
    app = create_app(settings, llm_client)
    executor = GuardedExecutor(settings)
    report = EvalReport()
    with TestClient(app) as client:
        for case in cases:
            result = _score_case(case, client, executor)
            report.results.append(result)
            if on_result is not None:
                on_result(result)
    return report

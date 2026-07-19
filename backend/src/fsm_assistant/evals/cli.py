"""`fsm-eval` entry point: runs an eval suite against the real LLM and prints
a metrics report (PRD story 31), also written to JSON for cross-version
tracking.

Two suites, both on demand and never part of the default test run:
`fsm-eval` (or `fsm-eval golden`) scores NL→SQL translation on the golden
question set; `fsm-eval rules` scores the end-to-end ability to draft Rules
that detect labeled fraud, against hand-computed backtest counts on the
pattern fixture database.

Metrics, not pass/fail: the Head of Fraud reads rates and repair-loop depth to
judge whether a change made the system better or worse across versions.
"""

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..llm import OpenAIClient
from .fixture import build_dataset_db
from .golden_set import GOLDEN_CASES
from .rule_patterns import (
    CUTOFF as PATTERN_CUTOFF,
    FIXTURE_LABELS,
    FIXTURE_TRANSACTIONS,
    RULE_PATTERNS,
)
from .rule_runner import RuleCaseResult, RuleEvalReport, run_rule_eval
from .runner import CaseResult, EvalReport, aggregate_reports, run_eval


def render(report: EvalReport) -> str:
    m = report.metrics()
    lines = ["", "=" * 68, "NL to SQL golden-set eval", "=" * 68]

    lines += [
        f"Cases:                        {m['cases']}  "
        f"({m['answerable']} answerable, {m['unanswerable']} unanswerable)",
        f"Match rate (answerable):      {_pct(m['match_rate'])}",
        f"Refusal rate (unanswerable):  {_pct(m['refusal_rate_on_unanswerable'])}",
        f"False-refusal rate:           {_pct(m['false_refusal_rate'])}",
        f"Error rate (answerable):      {_pct(m['error_rate'])}",
        f"SQL shape violation rate:     {_pct(m['sql_shape']['violation_rate'])}"
        + (f"  {m['sql_shape']['violations']}" if m["sql_shape"]["violations"] else ""),
        f"Single-line SQL rate:         {_pct(m['sql_shape']['single_line_sql_rate'])}",
    ]
    depth = m["repair_depth"]
    lines.append(
        f"Repair depth:                 mean {depth['mean_attempts']}, "
        f"max {depth['max_attempts']}, histogram {depth['attempts_histogram']}"
    )

    lines += ["", "By category:"]
    for cat, stats in m["by_category"].items():
        lines.append(
            f"  {cat:14} {stats['correct']}/{stats['n']}  {_pct(stats['correct_rate'])}"
        )

    lines += ["", "Per case:"]
    for r in report.results:
        mark = "PASS" if r.correct else "MISS"
        lines.append(
            f"  [{mark}] {r.id:34} {r.category:12} "
            f"attempts={r.attempts} status={r.status:8} {r.note}"
        )
    lines.append("")
    return "\n".join(lines)


def render_rules(report: RuleEvalReport) -> str:
    m = report.metrics()
    lines = ["", "=" * 68, "Rule-quality eval", "=" * 68]

    lines += [
        f"Patterns:                     {m['patterns']}  "
        f"({m['metric_patterns']} metric, {m['decline_patterns']} structural-decline)",
        # Headline: a decline on a segment-naming pattern vetoes the FSM's
        # hypothesis before the backtest can test it (ADR-0007).
        f"False decline rate (segment): {_pct(m['false_decline_rate'])}",
        f"Metrics match rate:           {_pct(m['match_rate'])}",
        f"Decline rate (structural):    {_pct(m['decline_rate_on_structural'])}",
        f"Error rate (metric):          {_pct(m['error_rate'])}",
    ]
    depth = m["draft_depth"]
    lines.append(
        f"Draft depth:                  mean {depth['mean_attempts']}, "
        f"max {depth['max_attempts']}, histogram {depth['attempts_histogram']}"
    )

    lines += ["", "Per pattern:"]
    for r in report.results:
        mark = "PASS" if r.correct else "MISS"
        lines.append(
            f"  [{mark}] {r.id:20} attempts={r.attempts} status={r.status:8} {r.note}"
        )
        if r.clause:
            lines.append(f"         clause: {r.clause}")
    lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def _write_report(prefix: str, settings: Settings, cutoff: str, report_dict: dict) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = settings.app_db_path.parent / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{prefix}-{timestamp}.json"
    payload = {
        "timestamp": timestamp,
        "model": settings.openai_model,
        "cutoff": cutoff,
        **report_dict,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"Metrics written to {out_path}")


def _print_progress(result: CaseResult | RuleCaseResult) -> None:
    mark = "PASS" if result.correct else "MISS"
    print(f"  [{mark}] {result.id} ({result.note})", flush=True)


def render_aggregate(aggregate: dict) -> str:
    lines = ["", "=" * 68, f"Golden-set flake report ({aggregate['runs']} runs)", "=" * 68]
    for label, key in (
        ("Match rate (answerable)", "match_rate"),
        ("SQL shape violation rate", "sql_shape_violation_rate"),
        ("Single-line SQL rate", "single_line_sql_rate"),
    ):
        s = aggregate[key]
        lines.append(
            f"{label + ':':30}mean {_pct(s['mean'])}  "
            f"(min {_pct(s['min'])}, max {_pct(s['max'])})"
        )
    lines += ["", "Per case (passes/runs):"]
    for case in aggregate["per_case"]:
        entry = f"  [{case['passes']:2}/{case['runs']:2}] {case['id']:34} {case['category']}"
        if case["failure_notes"]:
            entry += "  fails: " + "; ".join(case["failure_notes"])
        lines.append(entry)
    lines.append("")
    return "\n".join(lines)


def run_golden(runs: int = 1) -> int:
    settings = Settings()
    print(
        f"Running golden-set eval: {len(GOLDEN_CASES)} cases against real LLM "
        f"({settings.openai_model}), cutoff {settings.cutoff}"
        + (f", {runs} repeated runs." if runs > 1 else "."),
        flush=True,
    )
    llm = OpenAIClient(model=settings.openai_model)

    if runs == 1:
        report = run_eval(GOLDEN_CASES, settings, llm, on_result=_print_progress)
        print(render(report))
        _write_report("golden", settings, settings.cutoff, report.to_dict())
        return 0

    # Flake measurement (issue #14): repeat the identical set and report
    # per-case pass rates, since a wrong-denominator flake can pass any
    # single run by luck.
    reports: list[EvalReport] = []
    for i in range(1, runs + 1):
        print(f"\nRun {i}/{runs}:", flush=True)
        reports.append(run_eval(GOLDEN_CASES, settings, llm, on_result=_print_progress))
    aggregate = aggregate_reports(reports)
    print(render_aggregate(aggregate))
    _write_report(
        "golden",
        settings,
        settings.cutoff,
        {"aggregate": aggregate, "runs": [r.to_dict() for r in reports]},
    )
    return 0


def run_rules() -> int:
    # The pattern fixture is built fresh in a temp directory each run: the
    # expectations are hand-computed on exactly these rows, so the eval never
    # depends on the provided dataset or a stale local database.
    base = Settings()
    print(
        f"Running rule-quality eval: {len(RULE_PATTERNS)} patterns against real "
        f"LLM ({base.openai_model}), pattern fixture cutoff {PATTERN_CUTOFF}.",
        flush=True,
    )
    llm = OpenAIClient(model=base.openai_model)
    with tempfile.TemporaryDirectory(prefix="fsm-rule-eval-") as tmp:
        tmp_path = Path(tmp)
        data_db = tmp_path / "patterns.db"
        build_dataset_db(data_db, FIXTURE_TRANSACTIONS, FIXTURE_LABELS)
        settings = Settings(
            data_db_path=data_db,
            app_db_path=tmp_path / "app.db",
            cache_db_path=tmp_path / "cache.db",
            cutoff=PATTERN_CUTOFF,
        )
        report = run_rule_eval(RULE_PATTERNS, settings, llm, on_result=_print_progress)

    print(render_rules(report))
    # Reports land beside the golden set's, under the repo's var directory.
    _write_report("rules", base, PATTERN_CUTOFF, report.to_dict())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fsm-eval",
        description="Run an on-demand eval suite against the real LLM.",
    )
    parser.add_argument(
        "suite",
        nargs="?",
        choices=("golden", "rules"),
        default="golden",
        help="golden: NL->SQL golden set (default). rules: rule-quality patterns.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Repeat the golden set N times and report per-case pass rates "
        "(flake measurement).",
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.suite != "golden" and args.runs != 1:
        parser.error("--runs applies to the golden suite only")
    return run_golden(args.runs) if args.suite == "golden" else run_rules()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

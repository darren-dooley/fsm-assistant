"""The on-demand NL to SQL evaluation suite (PRD stories 29, 31).

Separate from the pytest test suite: it exercises the real LLM through the
same in-process HTTP seam the tests use, scores a golden question set by
execution-result match against the real database (never SQL string match),
and reports concrete metrics for cross-version comparison rather than
pass/fail. Run on demand with `uv run fsm-eval`; never part of `pytest`.
"""

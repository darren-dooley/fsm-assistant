"""The HTTP API. `create_app` takes the LLM client as an injectable
dependency: production wires in OpenAIClient, tests inject a scripted fake
and drive this app in-process (the PRD's primary testing seam)."""

from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .app_db import init_app_db
from .backtest import BacktestEngine, BacktestResult
from .config import Settings
from .guarded import GuardedExecutor, GuardedQueryError
from .llm import LLMClient
from .rule_store import RuleStore
from .summary_drafter import RunOutcome, SummaryDrafter
from .translator import ChatMessage, ChatOutcome, Translator


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    sql: str | None = None


class ExploreRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatMessageIn] = Field(default_factory=list)


class RunRequest(BaseModel):
    sql: str = Field(min_length=1)
    history: list[ChatMessageIn] = Field(default_factory=list)


class BacktestRequest(BaseModel):
    clause: str = Field(min_length=1)


class SaveRuleRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    clause: str = Field(min_length=1)


def create_app(settings: Settings, llm_client: LLMClient) -> FastAPI:
    init_app_db(settings.app_db_path)
    executor = GuardedExecutor(settings)
    translator = Translator(
        llm=llm_client,
        executor=executor,
        cutoff=settings.cutoff,
        row_limit=settings.row_limit,
        max_attempts=settings.max_translation_attempts,
    )
    summary_drafter = SummaryDrafter(
        llm=llm_client,
        executor=executor,
        cutoff=settings.cutoff,
        max_attempts=settings.max_translation_attempts,
    )
    backtester = BacktestEngine(executor)
    rules = RuleStore(settings.app_db_path)
    app = FastAPI(title="FSM Assistant")

    @lru_cache(maxsize=1)
    def dataset_meta() -> dict:
        result = executor.execute(
            "SELECT (SELECT COUNT(*) FROM transactions) AS transactions, "
            "COUNT(*) AS labeled, COALESCE(SUM(is_fraud), 0) AS fraud FROM fraud_labels"
        )
        transactions, labeled, fraud = result.rows[0]
        return {
            "cutoff": settings.cutoff,
            "transactions": transactions,
            "labeled": labeled,
            "fraud": fraud,
            "label_coverage_pct": round(100 * labeled / transactions, 1) if transactions else 0,
            "base_fraud_rate_pct": round(100 * fraud / labeled, 3) if labeled else 0,
            "row_limit": settings.row_limit,
            "query_timeout_ms": settings.query_timeout_ms,
        }

    @app.get("/api/meta")
    def meta() -> dict:
        return dataset_meta()

    def _to_messages(history: list[ChatMessageIn]) -> list[ChatMessage]:
        return [ChatMessage(role=m.role, content=m.content, sql=m.sql) for m in history]

    def _backtest(clause: str) -> tuple[BacktestResult | None, dict | None]:
        """Backtest a clause, translating clause rejection into a status body.
        Returns (result, None) on success or (None, error_body) otherwise."""
        try:
            return backtester.run(clause), None
        except GuardedQueryError as exc:
            status = "error" if exc.kind == "timeout" else "invalid"
            return None, {"status": status, "message": str(exc)}

    # Sync endpoints on purpose: FastAPI runs them on the threadpool, keeping
    # the event loop free while SQLite queries and LLM calls block.
    @app.post("/api/explore")
    def explore(request: ExploreRequest) -> ChatOutcome:
        return translator.chat(request.message, _to_messages(request.history))

    @app.post("/api/explore/run")
    def run_query(request: RunRequest) -> RunOutcome:
        # Execution is always FSM-initiated: chat only proposes and validates
        # SQL, this endpoint runs it (guarded), summarizes the real rows, and
        # drafts the run's rule artifacts from them (ADR-0006).
        return summary_drafter.run(request.sql, _to_messages(request.history))

    @app.post("/api/rules/backtest")
    def backtest_clause(request: BacktestRequest) -> dict:
        result, error = _backtest(request.clause)
        if error is not None:
            return error
        return {"status": "ok", "backtest": result.as_dict()}

    @app.get("/api/rules")
    def list_rules() -> dict:
        return {"rules": [rule.as_dict() for rule in rules.list()]}

    @app.post("/api/rules")
    def save_rule(request: SaveRuleRequest) -> dict:
        # The save gate: the exact clause is re-backtested server-side, so the
        # stored snapshot always matches the saved clause (PRD stories 14, 26).
        result, error = _backtest(request.clause)
        if error is not None:
            return error
        rule = rules.create(request.name, request.description, request.clause, result)
        return {"status": "ok", "rule": rule.as_dict()}

    @app.put("/api/rules/{rule_id}")
    def update_rule(rule_id: int, request: SaveRuleRequest) -> dict:
        # Editing reopens the Candidate: the fresh clause is re-backtested before
        # the change takes effect, so the rule set never holds unvalidated logic.
        if rules.get(rule_id) is None:
            raise HTTPException(status_code=404, detail="No such rule.")
        result, error = _backtest(request.clause)
        if error is not None:
            return error
        rule = rules.update(rule_id, request.name, request.description, request.clause, result)
        return {"status": "ok", "rule": rule.as_dict()}

    @app.post("/api/rules/{rule_id}/backtest")
    def rebacktest_rule(rule_id: int) -> dict:
        existing = rules.get(rule_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="No such rule.")
        result, error = _backtest(existing.clause)
        if error is not None:
            return error
        rule = rules.refresh_snapshot(rule_id, result)
        return {"status": "ok", "rule": rule.as_dict()}

    @app.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: int) -> dict:
        if not rules.delete(rule_id):
            raise HTTPException(status_code=404, detail="No such rule.")
        return {"status": "ok"}

    return app


def create_default_app() -> FastAPI:
    """Production wiring, used by `uv run fsm-assistant` and uvicorn."""
    from .llm import OpenAIClient

    settings = Settings()
    return create_app(settings, OpenAIClient(model=settings.openai_model))

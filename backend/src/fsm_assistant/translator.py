"""The Explore chat and its bounded repair loop.

The model converses with the FSM about the data. Not every turn yields SQL:
greetings, clarifications, and schema questions get a plain reply. When a
turn does call for data, the model proposes exactly one SELECT, which is
validated (compile-only, same guardrails as execution) but never executed
here — the FSM runs it from the UI via the run turn (`SummaryDrafter`),
which executes through the guarded executor and summarizes and drafts from
the real rows. Validation failures are fed back for re-translation, at most
`max_translation_attempts` times, every attempt passing the same guardrails
(PRD story 44). This is deliberately not an autonomous agent — the human
drives the loop.
"""

import json
from dataclasses import dataclass
from typing import Literal

from .guarded import GuardedExecutor, GuardedQueryError
from .llm import LLMClient, LLMUnavailableError

LLM_UNAVAILABLE_MESSAGE = (
    "The language model could not be reached. Please try again in a moment; "
    "if this keeps happening, check the backend's OpenAI configuration."
)


def strip_code_fence(reply: str) -> str:
    """Drop a ```/```json Markdown fence some models wrap JSON in, so the
    payload can be parsed. Shared by the chat and the run summary turn."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text


@dataclass(frozen=True)
class ChatMessage:
    """One prior chat turn, replayed for conversation context. Assistant
    turns carry the SQL they proposed, when they proposed any."""

    role: Literal["user", "assistant"]
    content: str = ""
    sql: str | None = None


@dataclass(frozen=True)
class ChatOutcome:
    status: Literal["ok", "error"]
    reply: str = ""
    message: str = ""
    sql: str | None = None
    attempts: int = 0


_CHAT_SYSTEM_PROMPT = """\
You are the exploration assistant for a Fraud Success Manager (FSM) working \
with transaction data. You chat with them in plain English and, when they \
want something computed from the data, you propose a SQLite query for them \
to run.

Database schema:

{schema}

Facts about the data:
- `date` columns are TEXT like 'YYYY-MM-DD HH:MM:SS'; compare them as strings.
- BOOLEAN columns store 0 or 1. Money columns are integer US cents.
- Only transactions before {cutoff} are visible; do not add your own date \
cutoff filters unless the FSM asks for one.
- `fraud_labels` covers only about two thirds of transactions. Join it only \
when the FSM's question is actually about fraud — a rate, a count, or a \
labeled-vs-unlabeled comparison — and then divide by the labeled count, \
never by all transactions. Use a plain JOIN for that; never \
`LEFT JOIN fraud_labels` with `COALESCE(is_fraud, 0)`, which counts \
unlabeled transactions as legitimate and understates every rate. A question \
about transaction attributes alone (amount, time, type, location, \
errors, ...) gets a plain query over `transactions`; do not join \
`fraud_labels` or add an `is_fraud` column "for context" when the FSM did \
not ask about fraud. Deployed rules cannot reference fraud labels, so a \
label-filtered query is one the FSM cannot carry forward into a rule.
- `transactions.errors` is NULL when a transaction had no error.

Rules:
- Reply with a single JSON object and nothing else.
- To just talk — greetings, clarifying what the FSM wants, explaining the \
schema or your approach, or saying why something cannot be answered from \
this data — reply {{"reply": "..."}} with one short plain-English paragraph \
and no other keys.
- When the FSM asks for something computable from the data, reply \
{{"reply": "...", "sql": "..."}} where `sql` is exactly one read-only SELECT \
(WITH ... SELECT is fine) and `reply` says in a sentence or two what the \
query computes. Never write, alter, or use PRAGMA/ATTACH.
- The query is not executed automatically: the FSM sees it with a Run \
button and decides whether to run it. Never claim results you have not seen.
- Results shown to the FSM are capped at {row_limit} rows, so aggregate or \
ORDER BY ... LIMIT rather than returning huge row sets.
- Use readable column aliases; round rates to 3 decimal places.
- Write the SQL across multiple lines: each clause (SELECT, FROM, JOIN, \
WHERE, GROUP BY, ORDER BY, LIMIT) starts its own line, so the FSM can read \
it without horizontal scrolling.
- If a question cannot be answered from this schema, say so in `reply` and \
include no `sql`. Never guess.\
"""

class Translator:
    def __init__(
        self,
        llm: LLMClient,
        executor: GuardedExecutor,
        cutoff: str,
        row_limit: int,
        max_attempts: int,
    ):
        self._llm = llm
        self._executor = executor
        self._cutoff = cutoff
        self._row_limit = row_limit
        self._max_attempts = max_attempts
        self._schema_ddl = executor.schema_ddl()

    def chat(self, message: str, history: list[ChatMessage]) -> ChatOutcome:
        messages = self._conversation(message, history)
        attempts = 0
        try:
            while True:
                attempts += 1
                reply = self._llm.complete(messages)
                parsed, parse_error = self._parse_reply(reply)

                if parse_error is not None:
                    failure = parse_error
                else:
                    text = str(parsed.get("reply", "")).strip()  # type: ignore[union-attr]
                    sql = parsed.get("sql")  # type: ignore[union-attr]
                    if sql is None:
                        return ChatOutcome(status="ok", reply=text, attempts=attempts)
                    sql = str(sql).strip()
                    try:
                        self._executor.validate_sql(sql)
                    except GuardedQueryError as exc:
                        failure = f"That query is not valid: {exc}"
                    else:
                        return ChatOutcome(
                            status="ok", reply=text, sql=sql, attempts=attempts
                        )

                if attempts >= self._max_attempts:
                    return ChatOutcome(
                        status="error",
                        message=(
                            "I couldn't produce a working query for that "
                            f"after {self._max_attempts} attempts. Try rephrasing it, "
                            "or asking about the data in smaller steps."
                        ),
                        attempts=attempts,
                    )
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {"role": "user", "content": f"{failure}\nReply with corrected JSON."}
                )
        except LLMUnavailableError:
            return ChatOutcome(
                status="error", message=LLM_UNAVAILABLE_MESSAGE, attempts=attempts
            )

    def _conversation(self, message: str, history: list[ChatMessage]) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": _CHAT_SYSTEM_PROMPT.format(
                    schema=self._schema_ddl, cutoff=self._cutoff, row_limit=self._row_limit
                ),
            }
        ]
        for turn in history:
            messages.append({"role": turn.role, "content": serialize_turn(turn)})
        messages.append({"role": "user", "content": message})
        return messages

    @staticmethod
    def _parse_reply(reply: str) -> tuple[dict | None, str | None]:
        try:
            parsed = json.loads(strip_code_fence(reply))
        except json.JSONDecodeError:
            return None, (
                "Your reply was not a valid JSON object of the form "
                '{"reply": "..."} or {"reply": "...", "sql": "..."}.'
            )
        if not isinstance(parsed, dict) or not ("reply" in parsed or "sql" in parsed):
            return None, 'Your JSON reply must contain a "reply" key (and optionally "sql").'
        return parsed, None


def serialize_turn(turn: ChatMessage) -> str:
    """Render a replayed turn the way the model was asked to speak: user
    turns as plain text, assistant turns as their JSON reply shape. Shared
    with the run summary turn so both prompts replay the same conversation."""
    if turn.role == "user":
        return turn.content
    payload: dict[str, str] = {"reply": turn.content}
    if turn.sql:
        payload["sql"] = turn.sql
    return json.dumps(payload)

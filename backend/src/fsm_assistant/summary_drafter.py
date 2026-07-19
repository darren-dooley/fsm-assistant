"""The Run turn: execute the FSM's query, summarize the real rows, and draft
the rule from them (ADR-0006, amended by ADR-0007).

One LLM call sees the whole Explore conversation, the SQL that ran, and the
real result rows, and returns the summary plus either a deployable rule
(clause, name, description) or a structural reason there is no deployable
segment to draft from. The turn's job is translation, not judgment: it renders
the segment the query identifies into a clause and never decides whether that
segment is risky — the backtest decides that, and the FSM runs it next. It
declines only when the run hands it no deployable segment: a whole-table
aggregate that names none, or a segment defined by the fraud label itself
(which a deployed rule can't reference). The clause is validated inline —
guardrails plus the fraud_labels deployability ban — inside the same bounded
repair loop the chat uses, so "Create a Rule" carries pre-validated artifacts
and the Workbench never calls the LLM. Failure never hides evidence: rows
always return, drafting exhaustion degrades to the summary plus a stated
reason, and an unreachable LLM keeps the fallback summary and carries no rule.
"""

import json
from dataclasses import dataclass, field
from typing import Literal

from .guarded import GuardedExecutor, GuardedQueryError, QueryResult
from .llm import LLMClient, LLMUnavailableError
from .translator import ChatMessage, serialize_turn, strip_code_fence

# How many result rows the combined prompt may see. The FSM always sees the
# real rows regardless; this only bounds prompt size.
_PROMPT_ROW_CAP = 50

FALLBACK_SUMMARY = "The query ran successfully; the results are shown below."


@dataclass(frozen=True)
class RuleDraft:
    clause: str
    name: str
    description: str


@dataclass(frozen=True)
class RunOutcome:
    """What running a query returns: the rows, the grounded summary, and the
    run's rule artifacts — a validated draft, or the structural reason the run
    names no deployable segment to draft from."""

    status: Literal["ok", "error"]
    message: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    summary: str = ""
    rule: RuleDraft | None = None
    decline_reason: str = ""
    attempts: int = 0


_SYSTEM_PROMPT = """\
You are helping a Fraud Success Manager (FSM) explore transaction data. They \
have been chatting with an assistant; the conversation is replayed below. The \
FSM then ran a query, and you receive the SQL and the real result rows. Your \
job is to summarize the results and translate the segment the query \
identifies into a deployable fraud Rule.

You do NOT decide whether that segment is risky — the backtest decides that, \
and the FSM runs it next. Draft a rule whenever the query identifies a \
filterable segment of transactions, even when these rows do not themselves \
prove the segment is fraudulent. Only decline when the query gives you no \
deployable segment to translate, for one of the two structural reasons stated \
at the end.

Database schema:

{schema}

Reply with a single JSON object and nothing else, in one of two shapes:
- {{"summary": "...", "rule": {{"clause": "...", "name": "...", \
"description": "..."}}}}
- {{"summary": "...", "decline": "one sentence stating the structural reason \
the run names no deployable segment to draft from"}}

The summary: one to three plain-English sentences answering the FSM's \
question. Use only numbers that appear in the rows or are directly computed \
from them — never invent values. Mention when results were truncated. State \
findings only; never comment on whether a rule can or should be created — \
the rule field speaks for itself.

The rule, when the results support one:
- `clause` is a single boolean SQL WHERE expression over the `transactions` \
table (SQLite) — no SELECT, no WHERE keyword, no semicolons.
- A rule runs in production against NEW transactions that have no fraud \
label yet. It must therefore NEVER reference `fraud_labels` or `is_fraud`, \
not even in a subquery. Instead, materialize what the results showed as \
literal predicates: if certain segments are high-risk, enumerate their \
values, e.g. `transaction_type = 'Online Transaction' AND \
merchant_location_id IN (101, 205)`. Take the literal values from the \
result rows or the conversation; never guess values that appear in neither.
- When the evidence is specific value COMBINATIONS (this type at that \
location), write one parenthesized AND-branch per evidenced combination \
joined with OR — never independent IN lists, whose cross-product would \
also match combinations the results show no evidence for. Two IN lists \
are only correct when every cross-combination genuinely appears in the \
rows.
- Subqueries over the dimension tables (cards, merchants, \
merchant_locations, mcc_codes, users) are allowed. When the rows are \
aggregate evidence about a named segment — say, a city with a high fraud \
rate — a rule IS supported: translate the segment name into transaction \
attributes with a dimension-table subquery, e.g. `merchant_location_id IN \
(SELECT id FROM merchant_locations WHERE city = 'Rome')`. Never write, \
alter, or use PRAGMA/ATTACH.
- Money columns are integer US cents, so convert every dollar figure by \
multiplying by 100 ($500 is `amount_usd_cents > 50000`). `date` columns are \
TEXT 'YYYY-MM-DD HH:MM:SS'; extract the hour with `strftime('%H', date)`, a \
two-digit 24-hour string ('00' to '23'). BOOLEAN columns store 0 or 1. \
`transactions.errors` is NULL when there was no error. Only transactions \
before {cutoff} are visible; do not add your own date cutoffs.
- If the rows were truncated, an enumerated list may be incomplete — say so \
in the description.
- `name` is a short title-case label; `description` is one to three plain \
sentences saying exactly what the clause matches and what evidence backs it.

Reply {{"summary": "...", "decline": "..."}} ONLY when the run hands you no \
deployable segment to translate, for one of two structural reasons: (1) the \
query summarizes the whole table and names no segment — a bare count, an \
average, a distribution with no filter — so there is nothing to filter on; or \
(2) the segment the query selects is defined by the fraud label itself (it \
filters on `fraud_labels` or `is_fraud`), which a deployed rule can never \
reference, so nothing deployable remains to carry. A query that only JOINs \
`fraud_labels` to measure fraud while filtering on transaction attributes \
still names a deployable segment — draft it. When a query mixes a label \
filter with deployable predicates, decline as a whole; do not drop the label \
filter and silently broaden the segment to something the FSM did not ask for. \
State the structural reason plainly. Never decline because the rows look \
unremarkable or the pattern seems weak — that verdict belongs to the \
backtest, not to you.\
"""


class SummaryDrafter:
    """The combined post-Run turn: summarize the real rows and draft the rule
    from them, validating the clause (deployability included) with the same
    bounded repair loop the chat uses."""

    def __init__(self, llm: LLMClient, executor: GuardedExecutor, cutoff: str, max_attempts: int):
        self._llm = llm
        self._executor = executor
        self._max_attempts = max_attempts
        self._system = _SYSTEM_PROMPT.format(schema=executor.schema_ddl(), cutoff=cutoff)

    def run(self, sql: str, history: list[ChatMessage]) -> RunOutcome:
        """Execute a proposed query on the FSM's explicit request, then
        summarize and draft from the real rows. Guardrails are enforced here
        regardless of where the SQL came from; a rejection is an error, never
        a repair."""
        try:
            result = self._executor.execute(sql)
        except GuardedQueryError as exc:
            return RunOutcome(status="error", message=str(exc))
        return self._summarize_and_draft(sql, history, result)

    def _summarize_and_draft(
        self, sql: str, history: list[ChatMessage], result: QueryResult
    ) -> RunOutcome:
        def outcome(**kwargs) -> RunOutcome:
            return RunOutcome(
                status="ok",
                columns=result.columns,
                rows=result.rows,
                truncated=result.truncated,
                **kwargs,
            )

        conversation = self._conversation(sql, history, result)
        attempts = 0
        last_summary = ""
        try:
            while True:
                attempts += 1
                reply = self._llm.complete(conversation)
                parsed, failure = self._parse(reply)

                if parsed is not None:
                    last_summary = str(parsed.get("summary", "")).strip()
                    if "decline" in parsed:
                        return outcome(
                            summary=last_summary,
                            decline_reason=str(parsed["decline"]).strip(),
                            attempts=attempts,
                        )
                    rule = parsed.get("rule") or {}
                    clause = str(rule.get("clause", "")).strip()
                    try:
                        self._executor.validate_clause(clause)
                    except GuardedQueryError as exc:
                        failure = f"That clause is not valid: {exc}"
                    else:
                        return outcome(
                            summary=last_summary,
                            rule=RuleDraft(
                                clause=clause,
                                name=str(rule.get("name", "")).strip(),
                                description=str(rule.get("description", "")).strip(),
                            ),
                            attempts=attempts,
                        )

                if attempts >= self._max_attempts:
                    # The summary must never be lost to a drafting failure:
                    # degrade to summary-plus-decline, stating what happened.
                    return outcome(
                        summary=last_summary or FALLBACK_SUMMARY,
                        decline_reason=(
                            f"Couldn't draft a valid rule after {self._max_attempts} "
                            f"attempts. Last problem: {failure}"
                        ),
                        attempts=attempts,
                    )
                conversation.append({"role": "assistant", "content": reply})
                conversation.append(
                    {"role": "user", "content": f"{failure}\nReply with corrected JSON."}
                )
        except LLMUnavailableError:
            # Rows always return: keep the fallback summary and carry no rule,
            # with a stated reason so the run card shows drafting was skipped
            # rather than silently offering nothing.
            return outcome(
                summary=FALLBACK_SUMMARY,
                decline_reason=(
                    "The language model could not be reached, so this run has "
                    "no draft. Re-run the query to try again."
                ),
                attempts=attempts,
            )

    def _conversation(
        self, sql: str, history: list[ChatMessage], result: QueryResult
    ) -> list[dict[str, str]]:
        conversation = [{"role": "system", "content": self._system}]
        # Replay the whole Explore conversation, not just the last question,
        # so constraints stated in earlier turns reach the draft.
        for turn in history:
            conversation.append({"role": turn.role, "content": serialize_turn(turn)})
        payload = json.dumps(
            {
                "ran_sql": sql,
                "columns": result.columns,
                "rows": result.rows[:_PROMPT_ROW_CAP],
                "truncated_to_row_cap": result.truncated
                or len(result.rows) > _PROMPT_ROW_CAP,
            }
        )
        conversation.append({"role": "user", "content": payload})
        return conversation

    @staticmethod
    def _parse(reply: str) -> tuple[dict | None, str | None]:
        try:
            parsed = json.loads(strip_code_fence(reply))
        except json.JSONDecodeError:
            return None, (
                "Your reply was not a valid JSON object of the form "
                '{"summary": "...", "rule": {...}} or {"summary": "...", "decline": "..."}.'
            )
        if not isinstance(parsed, dict) or "summary" not in parsed:
            return None, 'Your JSON reply must contain a "summary" key.'
        if "rule" not in parsed and "decline" not in parsed:
            return None, 'Your JSON reply must contain either a "rule" or a "decline" key.'
        return parsed, None

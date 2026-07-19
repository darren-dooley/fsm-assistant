import { useEffect, useRef, useState } from 'react'
import type { ChatMessage, Meta, RuleDraft } from './api'
import { BACKEND_UNREACHABLE, chat, runQuery } from './api'

// Each run owns its artifacts: the rule (or the reason there isn't one) is a
// snapshot of that run's rows. Chat after a run never mutates it; re-running
// regenerates both summary and draft (ADR-0006).
type RunState =
  | { status: 'running' }
  | { status: 'error'; message: string }
  | {
      status: 'done'
      columns: string[]
      rows: (string | number | null)[][]
      truncated: boolean
      rule: RuleDraft | null
      declineReason: string
    }

// The rendered thread: chat turns plus error cards. Error items are shown but
// never replayed to the backend as conversation history.
interface Item {
  kind: 'user' | 'assistant' | 'error'
  content: string
  sql: string | null
  attempts?: number
  run?: RunState
}

function toHistory(items: Item[]): ChatMessage[] {
  return items
    .filter((item) => item.kind !== 'error')
    .map((item) => ({
      role: item.kind === 'user' ? ('user' as const) : ('assistant' as const),
      content: item.content,
      sql: item.sql,
    }))
}

interface ExploreProps {
  meta: Meta | null
  backendDown: boolean
  onCreateRule: (rule: RuleDraft) => void
}

export default function Explore({ meta, backendDown, onCreateRule }: ExploreProps) {
  const [items, setItems] = useState<Item[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const threadEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items, busy])

  const anyRunning = items.some((item) => item.run?.status === 'running')

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = input.trim()
    if (!trimmed || busy) return
    setBusy(true)
    setInput('')
    const history = toHistory(items)
    setItems((prev) => [...prev, { kind: 'user', content: trimmed, sql: null }])
    try {
      const response = await chat(trimmed, history)
      if (response.status === 'ok') {
        setItems((prev) => [
          ...prev,
          { kind: 'assistant', content: response.reply, sql: response.sql, attempts: response.attempts },
        ])
      } else {
        setItems((prev) => [...prev, { kind: 'error', content: response.message, sql: null }])
      }
    } catch {
      setItems((prev) => [...prev, { kind: 'error', content: BACKEND_UNREACHABLE, sql: null }])
    }
    setBusy(false)
  }

  // Run is always FSM-initiated: the proposed query executes only on click,
  // and the summary of the real rows joins the thread as an assistant turn so
  // follow-up questions can build on what the query returned.
  async function run(index: number) {
    const sql = items[index].sql
    if (!sql || anyRunning) return
    setItems((prev) =>
      prev.map((item, i) => (i === index ? { ...item, run: { status: 'running' } } : item)),
    )
    const history = toHistory(items.slice(0, index + 1))
    try {
      const response = await runQuery(sql, history)
      if (response.status === 'ok') {
        setItems((prev) => [
          ...prev.map((item, i) =>
            i === index
              ? {
                  ...item,
                  run: {
                    status: 'done' as const,
                    columns: response.columns,
                    rows: response.rows,
                    truncated: response.truncated,
                    rule: response.rule,
                    declineReason: response.decline_reason,
                  },
                }
              : item,
          ),
          { kind: 'assistant', content: response.summary, sql: null },
        ])
      } else {
        setItems((prev) =>
          prev.map((item, i) =>
            i === index ? { ...item, run: { status: 'error' as const, message: response.message } } : item,
          ),
        )
      }
    } catch {
      setItems((prev) =>
        prev.map((item, i) =>
          i === index ? { ...item, run: { status: 'error' as const, message: BACKEND_UNREACHABLE } } : item,
        ),
      )
    }
  }

  function createRule(index: number) {
    const run = items[index].run
    if (run?.status === 'done' && run.rule) onCreateRule(run.rule)
  }

  return (
    <>
      <main className="thread">
        {meta && (
          <p className="dataset-facts">
            {meta.transactions.toLocaleString()} transactions before {meta.cutoff} ·{' '}
            {meta.label_coverage_pct}% carry a fraud label · base fraud rate{' '}
            <b>{meta.base_fraud_rate_pct}%</b> of labeled transactions. Chat about the data in
            plain English; when the assistant proposes a query, you decide whether to run it.
          </p>
        )}
        {backendDown && (
          <div className="card error-card">
            The assistant backend could not be reached. Start it with{' '}
            <code>uv run fsm-assistant</code> and reload this page.
          </div>
        )}
        {items.map((item, i) => (
          <ItemView
            key={i}
            item={item}
            runDisabled={anyRunning}
            onRun={() => run(i)}
            onCreateRule={() => createRule(i)}
          />
        ))}
        {busy && <div className="card pending">Thinking…</div>}
        <div ref={threadEndRef} />
      </main>

      <form className="ask" onSubmit={send}>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Message the assistant about the transaction data…"
          aria-label="Message"
          disabled={backendDown}
        />
        <button type="submit" disabled={busy || backendDown || !input.trim()}>
          {busy ? 'Working…' : 'Send'}
        </button>
      </form>
    </>
  )
}

function ItemView({
  item,
  runDisabled,
  onRun,
  onCreateRule,
}: {
  item: Item
  runDisabled: boolean
  onRun: () => void
  onCreateRule: () => void
}) {
  if (item.kind === 'user') return <div className="user-msg">{item.content}</div>
  if (item.kind === 'error') return <div className="card error-card">{item.content}</div>
  return (
    <div className="card">
      {item.content && <p className="commentary">{item.content}</p>}
      {item.sql && (
        <>
          <pre className="sql">{item.sql}</pre>
          {(item.attempts ?? 1) > 1 && (
            <p className="fine-print">
              Took {item.attempts} attempts — earlier translations failed validation and were
              repaired.
            </p>
          )}
          <QueryActions
            run={item.run}
            runDisabled={runDisabled}
            onRun={onRun}
            onCreateRule={onCreateRule}
          />
        </>
      )}
    </div>
  )
}

function QueryActions({
  run,
  runDisabled,
  onRun,
  onCreateRule,
}: {
  run: RunState | undefined
  runDisabled: boolean
  onRun: () => void
  onCreateRule: () => void
}) {
  if (run?.status === 'done') {
    return (
      <>
        <ResultTable columns={run.columns} rows={run.rows} truncated={run.truncated} />
        {run.rule ? (
          <div className="answer-actions">
            <button type="button" className="btn-link" onClick={onCreateRule}>
              Create a Rule from this →
            </button>
          </div>
        ) : (
          run.declineReason && <p className="fine-print">No rule drafted: {run.declineReason}</p>
        )}
        {/* Re-running regenerates the summary and the draft — the artifacts
            are a snapshot of their run, and this is the path to refresh them
            (e.g. after the model was unreachable). */}
        <div className="run-actions">
          <button type="button" className="btn-link" onClick={onRun} disabled={runDisabled}>
            Run again
          </button>
        </div>
      </>
    )
  }
  return (
    <>
      {run?.status === 'error' && <div className="card error-card">{run.message}</div>}
      <div className="run-actions">
        <button
          type="button"
          onClick={onRun}
          disabled={runDisabled || run?.status === 'running'}
        >
          {run?.status === 'running'
            ? 'Running…'
            : run?.status === 'error'
              ? 'Run again'
              : 'Run query'}
        </button>
      </div>
    </>
  )
}

function ResultTable({
  columns,
  rows,
  truncated,
}: {
  columns: string[]
  rows: (string | number | null)[][]
  truncated: boolean
}) {
  if (rows.length === 0) {
    return <p className="fine-print">The query returned no rows.</p>
  }
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell === null ? '∅' : String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className="fine-print">
          Showing the first {rows.length} rows — the full result was larger.
        </p>
      )}
    </>
  )
}

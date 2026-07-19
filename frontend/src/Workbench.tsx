import { useEffect, useState } from 'react'
import type { Backtest } from './api'
import { BACKEND_UNREACHABLE, backtestClause, saveRule, updateRule } from './api'
import { EvidencePanel } from './evidence'
import type { Handoff } from './handoff'

interface WorkbenchProps {
  handoff: Handoff | null
  handoffKey: number
  backendDown: boolean
  onSaved: () => void
}

export default function Workbench({ handoff, handoffKey, backendDown, onSaved }: WorkbenchProps) {
  const [editingId, setEditingId] = useState<number | null>(null)
  const [provenance, setProvenance] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [clause, setClause] = useState('')
  const [backtest, setBacktest] = useState<Backtest | null>(null)
  const [backtestedClause, setBacktestedClause] = useState<string | null>(null)
  const [backtesting, setBacktesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  // React to a new handoff. A draft handoff carries the run's pre-validated
  // artifacts and fills the fields instantly — no LLM call happens in this
  // tab (ADR-0006); an edit handoff loads the saved Rule as a fresh Candidate
  // with its evidence discarded (PRD stories 10, 26). Name and description
  // arrive pre-filled but stay editable text the FSM can refine before saving.
  useEffect(() => {
    if (!handoff) return
    setMessage(null)
    setBacktest(null)
    setBacktestedClause(null)
    if (handoff.kind === 'draft') {
      setEditingId(null)
      setProvenance(true)
    } else {
      setEditingId(handoff.rule.id)
      setProvenance(false)
    }
    setName(handoff.rule.name)
    setDescription(handoff.rule.description)
    setClause(handoff.rule.clause)
    // Re-initialize only on a new handoff, keyed by handoffKey; the effect
    // reads the latest handoff from props on each run. handoffKey is bumped in
    // lockstep with every setHandoff, so keying on it alone is sufficient.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [handoffKey])

  async function runBacktest() {
    const trimmed = clause.trim()
    if (!trimmed || backendDown) return
    setBacktesting(true)
    setMessage(null)
    try {
      const result = await backtestClause(trimmed)
      if (result.status === 'ok') {
        setBacktest(result.backtest)
        setBacktestedClause(trimmed)
      } else {
        setBacktest(null)
        setBacktestedClause(null)
        setMessage(result.message)
      }
    } catch {
      setMessage(BACKEND_UNREACHABLE)
    } finally {
      setBacktesting(false)
    }
  }

  async function save() {
    if (!canSave) return
    setSaving(true)
    setMessage(null)
    const payload = { name: name.trim(), description: description.trim(), clause: clause.trim() }
    try {
      const result = editingId === null ? await saveRule(payload) : await updateRule(editingId, payload)
      if (result.status === 'ok') {
        onSaved()
      } else {
        setMessage(result.message)
      }
    } catch {
      setMessage(BACKEND_UNREACHABLE)
    } finally {
      setSaving(false)
    }
  }

  const busy = backtesting || saving
  // The evidence gate: the Backtest must be of the exact clause being saved.
  const evidenceMatchesClause = backtest !== null && backtestedClause === clause.trim()
  const clauseEdited = backtest !== null && backtestedClause !== clause.trim()
  const canSave = evidenceMatchesClause && name.trim().length > 0 && !busy && !backendDown

  // A cold start (no clause yet) still shows the full form so an FSM can
  // write a clause by hand, without prior exploration (PRD story 11).
  const coldStart = clause.length === 0 && !provenance

  return (
    <main className="thread">
      <div className="wb">
        {provenance ? (
          <p className="wb-provenance">▸ drafted from your Explore run</p>
        ) : (
          coldStart && (
            <p className="wb-provenance">
              Write a WHERE clause below, or come from an Explore query’s <b>Create a Rule</b>.
            </p>
          )
        )}

        <label className="wb-field">
          <span className="wb-label">Rule name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this rule"
            disabled={backendDown}
          />
        </label>

        <label className="wb-field">
          <span className="wb-label">
            WHERE clause <small>— the deployable rule, edit freely</small>
          </span>
          <textarea
            className="wb-where"
            value={clause}
            onChange={(e) => setClause(e.target.value)}
            placeholder="amount_usd_cents > 50000 AND transaction_type = 'Online Transaction'"
            rows={3}
            spellCheck={false}
            disabled={backendDown}
          />
        </label>

        <label className="wb-field">
          <span className="wb-label">Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What this rule is for (optional)"
            disabled={backendDown}
          />
        </label>

        <div className="wb-sect">Backtest · pre-T labeled transactions</div>
        <div className="wb-backtest-row">
          <button
            type="button"
            onClick={runBacktest}
            disabled={busy || backendDown || !clause.trim()}
          >
            {backtesting ? 'Running…' : 'Run backtest'}
          </button>
          {clauseEdited && (
            <span className="wb-stale">Clause changed since the last backtest — re-run to update the evidence.</span>
          )}
        </div>

        {message && <div className="card error-card wb-message">{message}</div>}
        {backtest && evidenceMatchesClause && <EvidencePanel backtest={backtest} />}

        <div className="wb-save">
          <button type="button" className="wb-primary" onClick={save} disabled={!canSave}>
            {saving ? 'Saving…' : editingId === null ? 'Create Rule' : 'Save changes'}
          </button>
          {!evidenceMatchesClause && (
            <span className="wb-gate">Backtest the exact clause before saving.</span>
          )}
        </div>
      </div>
    </main>
  )
}

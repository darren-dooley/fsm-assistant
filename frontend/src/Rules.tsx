import { useCallback, useEffect, useState } from 'react'
import type { Rule } from './api'
import { deleteRule, listRules, rebacktestRule } from './api'
import { ScoreBand } from './evidence'
import { evidenceLine } from './score'

interface RulesProps {
  active: boolean
  refreshKey: number
  backendDown: boolean
  onEdit: (rule: Rule) => void
}

export default function Rules({ active, refreshKey, backendDown, onEdit }: RulesProps) {
  const [rules, setRules] = useState<Rule[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRules(await listRules())
      setError(null)
    } catch {
      setError('Could not load the rule set. Check that the backend is running.')
    } finally {
      setLoaded(true)
    }
  }, [])

  // Refetch whenever the tab becomes active or a save bumps the refresh key,
  // so the ranked set always reflects the latest evidence.
  useEffect(() => {
    if (active && !backendDown) void load()
  }, [active, refreshKey, backendDown, load])

  async function rebacktest(id: number) {
    setBusyId(id)
    try {
      const result = await rebacktestRule(id)
      if (result.status === 'ok') {
        // Match the server's ranking exactly: Score desc, id desc as tiebreak.
        setRules((prev) =>
          [...prev.map((r) => (r.id === id ? result.rule : r))].sort(
            (a, b) => b.score - a.score || b.id - a.id,
          ),
        )
      } else {
        setError(result.message)
      }
    } catch {
      setError('Re-backtest failed. Check that the backend is running.')
    } finally {
      setBusyId(null)
    }
  }

  async function remove(id: number) {
    setBusyId(id)
    try {
      await deleteRule(id)
      setRules((prev) => prev.filter((r) => r.id !== id))
    } catch {
      setError('Delete failed. Check that the backend is running.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="thread">
      <div className="wb-rule-head">
        <span className="wb-label">
          {rules.length} saved · ranked by Score
        </span>
      </div>
      {error && <div className="card error-card">{error}</div>}
      {loaded && rules.length === 0 && !error && (
        <div className="card wb-empty">
          <p>
            No saved Rules yet. In <b>Explore</b>, ask a question and choose{' '}
            <b>Create a Rule from this</b> to draft, backtest, and save your first Rule.
          </p>
        </div>
      )}
      {rules.map((rule) => (
        <div className="wf-card" key={rule.id}>
          <div className="wf-card-head">
            <div>
              <div className="wf-card-title">{rule.name}</div>
              <div className="wf-evidence">saved {rule.created_at?.slice(0, 10)}</div>
            </div>
            <ScoreBand score={rule.score} compact />
          </div>
          <pre className="wb-where small">{rule.clause}</pre>
          {rule.description && <p className="fine-print">{rule.description}</p>}
          <p className="wf-evidence">{evidenceLine(rule.backtest)}</p>
          <div className="wf-actions">
            <button type="button" onClick={() => rebacktest(rule.id)} disabled={busyId === rule.id || backendDown}>
              {busyId === rule.id ? '…' : 'Re-backtest'}
            </button>
            <button type="button" onClick={() => onEdit(rule)} disabled={backendDown}>
              Edit
            </button>
            <button type="button" onClick={() => remove(rule.id)} disabled={busyId === rule.id || backendDown}>
              Delete
            </button>
          </div>
        </div>
      ))}
    </main>
  )
}

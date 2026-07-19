import { useEffect, useState } from 'react'
import type { Meta, Rule, RuleDraft } from './api'
import { fetchMeta } from './api'
import Explore from './Explore'
import type { Handoff } from './handoff'
import Rules from './Rules'
import Workbench from './Workbench'
import './App.css'

const TABS = ['Explore', 'Rule Workbench', 'Rules'] as const
type Tab = (typeof TABS)[number]

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [backendDown, setBackendDown] = useState(false)
  const [tab, setTab] = useState<Tab>('Explore')
  const [handoff, setHandoff] = useState<Handoff | null>(null)
  const [handoffKey, setHandoffKey] = useState(0)
  const [rulesRefreshKey, setRulesRefreshKey] = useState(0)

  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch(() => setBackendDown(true))
  }, [])

  function createRule(rule: RuleDraft) {
    // Pure navigation: the run already drafted and validated the artifacts,
    // so the Workbench pre-fills instantly with no LLM call (ADR-0006).
    setHandoff({ kind: 'draft', rule })
    setHandoffKey((k) => k + 1)
    setTab('Rule Workbench')
  }

  function editRule(rule: Rule) {
    setHandoff({ kind: 'edit', rule })
    setHandoffKey((k) => k + 1)
    setTab('Rule Workbench')
  }

  function onSaved() {
    setRulesRefreshKey((k) => k + 1)
    setTab('Rules')
  }

  return (
    <div className="app">
      <header className="masthead">
        <span className="masthead-title">FSM Assistant</span>
        <span className="masthead-note">explore · draft · backtest · save — pre-cutoff data only</span>
      </header>
      <nav className="tabs">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            className={name === tab ? 'tab on' : 'tab off'}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
        <span className="tab off disabled">
          Monitoring<small> soon</small>
        </span>
      </nav>

      {/* Panes stay mounted (display:contents) so the Explore conversation and
          an in-progress Candidate survive tab switches. */}
      <div className="pane" hidden={tab !== 'Explore'}>
        <Explore meta={meta} backendDown={backendDown} onCreateRule={createRule} />
      </div>
      <div className="pane" hidden={tab !== 'Rule Workbench'}>
        <Workbench
          handoff={handoff}
          handoffKey={handoffKey}
          backendDown={backendDown}
          onSaved={onSaved}
        />
      </div>
      <div className="pane" hidden={tab !== 'Rules'}>
        <Rules
          active={tab === 'Rules'}
          refreshKey={rulesRefreshKey}
          backendDown={backendDown}
          onEdit={editRule}
        />
      </div>
    </div>
  )
}

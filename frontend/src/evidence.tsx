import type { Backtest } from './api'
import { pct, scoreBand, tradeoffSentence, liftLabel } from './score'

export function ScoreBand({ score, compact = false }: { score: number; compact?: boolean }) {
  const rounded = Math.round(score)
  return (
    <div className={compact ? 'score compact' : 'score'}>
      <div className="score-num">
        {rounded}
        <span className="score-den">/100</span>
      </div>
      <div className="score-band">{scoreBand(score)}</div>
      <div className="score-bar">
        <div className="score-fill" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </div>
    </div>
  )
}

export function EvidencePanel({ backtest }: { backtest: Backtest }) {
  const b = backtest
  const tiles: [string, string][] = [
    [b.fraud_caught.toLocaleString(), 'fraud caught'],
    [b.legit_blocked.toLocaleString(), 'legit blocked'],
    [pct(b.precision), 'precision'],
    [pct(b.recall), 'of known fraud'],
    [liftLabel(b.lift), 'lift vs base'],
    [b.flagged_unlabeled.toLocaleString(), 'flagged, unlabeled'],
  ]
  return (
    <div className="evidence">
      <div className="evidence-top">
        <ScoreBand score={b.score} />
        <div className="tiles">
          {tiles.map(([value, caption]) => (
            <div className="tile" key={caption}>
              <div className="tile-val">{value}</div>
              <div className="tile-cap">{caption}</div>
            </div>
          ))}
        </div>
      </div>
      <p className="tradeoff">{tradeoffSentence(b)}</p>
      <p className="evidence-basis">
        {b.evidence_basis} evidence — optimistic by construction · metrics over{' '}
        {b.flagged_labeled.toLocaleString()} labeled of{' '}
        {b.flagged_total.toLocaleString()} flagged ({b.flagged_unlabeled.toLocaleString()} unlabeled
        excluded) · base rate {pct(b.base_rate, 3)}
      </p>
    </div>
  )
}

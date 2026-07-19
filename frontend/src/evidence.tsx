import type { Backtest } from './api'
import { pct, scoreBand, tradeoffSentence } from './score'

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
    </div>
  )
}

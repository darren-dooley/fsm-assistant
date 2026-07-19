import type { Backtest } from './api'

// Score bands are a display aid over the 0-100 Score; the number and its
// components are always shown, so the band never stands alone.
export function scoreBand(score: number): string {
  if (score >= 80) return 'EXCELLENT'
  if (score >= 60) return 'STRONG'
  if (score >= 40) return 'FAIR'
  return 'WEAK'
}

export function pct(x: number, dp = 2): string {
  return `${(100 * x).toFixed(dp)}%`
}

export function liftLabel(lift: number): string {
  return `${lift.toFixed(lift >= 10 ? 0 : 1)}×`
}

// The plain-English tradeoff (legitimate blocked per fraud caught), shown with
// the Score, never in its place. Raw counts sit beside ratios because
// percentages mislead at a ~0.17% base rate.
export function tradeoffSentence(b: Backtest): string {
  if (b.fraud_caught === 0) {
    return 'This clause catches none of the known fraud in the pre-cutoff data — its evidence does not yet support saving.'
  }
  const per = b.legit_blocked_per_fraud_caught
  if (per === null || per === 0) {
    return 'This rule blocks no labeled-legitimate payments for the fraud it catches — a clean tradeoff on this evidence.'
  }
  const rounded = Math.round(per)
  const plural = rounded === 1 ? 'payment' : 'payments'
  return `Add this rule if you are willing to block about ${rounded} legitimate ${plural} for every 1 fraud it catches.`
}

// A one-line evidence summary for the Rules tab cards.
export function evidenceLine(b: Backtest): string {
  return `${pct(b.precision)} precision · ${pct(b.recall)} of known fraud · ${liftLabel(b.lift)} lift`
}

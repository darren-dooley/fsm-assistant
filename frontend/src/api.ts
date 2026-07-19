export const BACKEND_UNREACHABLE =
  'The assistant backend could not be reached. Check that it is running, then try again.'

export interface Meta {
  cutoff: string
  transactions: number
  labeled: number
  fraud: number
  label_coverage_pct: number
  base_fraud_rate_pct: number
  row_limit: number
  query_timeout_ms: number
}

// One Explore chat turn. Assistant turns carry the SQL they proposed, when
// they proposed any; the whole thread is replayed to the backend for context.
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sql: string | null
}

export interface ChatResponse {
  status: 'ok' | 'error'
  reply: string
  message: string
  sql: string | null
  attempts: number
}

// A run's rule artifacts: drafted from the real rows and pre-validated
// server-side, so "Create a Rule" is pure navigation (ADR-0006).
export interface RuleDraft {
  clause: string
  name: string
  description: string
}

export type RunResponse =
  | {
      status: 'ok'
      columns: string[]
      rows: (string | number | null)[][]
      truncated: boolean
      summary: string
      rule: RuleDraft | null
      decline_reason: string
      attempts: number
    }
  | { status: 'error'; message: string }

export interface Backtest {
  flagged_total: number
  flagged_labeled: number
  flagged_unlabeled: number
  fraud_caught: number
  legit_blocked: number
  labeled_total: number
  fraud_total: number
  precision: number
  recall: number
  base_rate: number
  lift: number
  legit_blocked_per_fraud_caught: number | null
  score: number
  evidence_basis: string
}

export interface Rule {
  id: number
  name: string
  description: string
  clause: string
  score: number
  backtest: Backtest
  created_at: string
  updated_at: string
}

// The backtest/save/update endpoints share a status-tagged shape: an "ok"
// carries evidence (a Backtest or a saved Rule); anything else carries a
// plain-English message.
export type BacktestResponse =
  | { status: 'ok'; backtest: Backtest }
  | { status: 'invalid' | 'error'; message: string }

export type RuleResponse =
  | { status: 'ok'; rule: Rule }
  | { status: 'invalid' | 'error'; message: string }

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`${url} failed: ${response.status}`)
  return response.json()
}

export async function fetchMeta(): Promise<Meta> {
  const response = await fetch('/api/meta')
  if (!response.ok) throw new Error(`meta failed: ${response.status}`)
  return response.json()
}

export async function chat(message: string, history: ChatMessage[]): Promise<ChatResponse> {
  return postJson('/api/explore', { message, history })
}

export async function runQuery(sql: string, history: ChatMessage[]): Promise<RunResponse> {
  return postJson('/api/explore/run', { sql, history })
}

export async function backtestClause(clause: string): Promise<BacktestResponse> {
  return postJson('/api/rules/backtest', { clause })
}

interface RulePayload {
  name: string
  description: string
  clause: string
}

export async function saveRule(payload: RulePayload): Promise<RuleResponse> {
  return postJson('/api/rules', payload)
}

export async function updateRule(id: number, payload: RulePayload): Promise<RuleResponse> {
  const response = await fetch(`/api/rules/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`update rule failed: ${response.status}`)
  return response.json()
}

export async function rebacktestRule(id: number): Promise<RuleResponse> {
  return postJson(`/api/rules/${id}/backtest`, {})
}

export async function listRules(): Promise<Rule[]> {
  const response = await fetch('/api/rules')
  if (!response.ok) throw new Error(`list rules failed: ${response.status}`)
  return (await response.json()).rules
}

export async function deleteRule(id: number): Promise<void> {
  const response = await fetch(`/api/rules/${id}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`delete rule failed: ${response.status}`)
}

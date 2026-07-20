// Thin client for the localhost admin API + the SSE change feed.
import { useEffect, useState, useCallback, useRef } from 'preact/hooks'

async function req(path, opts) {
  const r = await fetch(path, opts)
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export const api = {
  state: () => req('/admin/state'),
  requests: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== '')),
    )
    return req(`/admin/requests?${q}`)
  },
  series: (days = 30) => req(`/admin/series?days=${days}`),
  doctor: (probe = false) => req(`/admin/doctor${probe ? '?probe=1' : ''}`),
  settings: () => req('/admin/settings'),
  saveSettings: (body) => req('/admin/settings', { method: 'POST', body: JSON.stringify(body) }),
  createKey: (label) => req('/admin/keys', { method: 'POST', body: JSON.stringify({ label }) }),
  deleteKey: (key) => req('/admin/keys/delete', { method: 'POST', body: JSON.stringify({ key }) }),
  forgetSession: (key) =>
    req('/admin/sessions/forget', { method: 'POST', body: JSON.stringify({ key }) }),
  keyFailover: (key, mode) =>
    req('/admin/keys/failover', { method: 'POST', body: JSON.stringify({ key, mode }) }),
  rescan: () => req('/admin/doctor/rescan', { method: 'POST', body: '{}' }),
  probe: () => req('/admin/doctor/probe', { method: 'POST', body: '{}' }),
  health: () => req('/health'),
  accounts: (probe = false) => req(`/admin/accounts${probe ? '?probe=1' : ''}`),
  accountAdd: (label, backend) =>
    req('/admin/accounts', { method: 'POST', body: JSON.stringify({ label, backend }) }),
  accountUpdate: (id, patch) =>
    req('/admin/accounts/update', { method: 'POST', body: JSON.stringify({ id, ...patch }) }),
  accountDelete: (id) =>
    req('/admin/accounts/delete', { method: 'POST', body: JSON.stringify({ id }) }),
  accountPin: (id) =>
    req('/admin/accounts/pin', { method: 'POST', body: JSON.stringify({ id }) }),
  accountProbe: (id) =>
    req('/admin/accounts/probe', { method: 'POST', body: JSON.stringify({ id }) }),
}

// One shared EventSource; components subscribe to change ticks. Auto-reconnects
// (EventSource does that natively) so a server restart heals itself.
const listeners = new Set()
let source = null

function ensureSource() {
  if (source) return
  source = new EventSource('/admin/events')
  const tick = (kind) => (e) => {
    let data = {}
    try { data = JSON.parse(e.data || '{}') } catch {}
    listeners.forEach((fn) => fn(kind, data))
  }
  source.addEventListener('request', tick('request'))
  source.addEventListener('state', tick('state'))
}

export function useLive(onChange) {
  const cb = useRef(onChange)
  cb.current = onChange
  useEffect(() => {
    ensureSource()
    const fn = (kind, data) => cb.current?.(kind, data)
    listeners.add(fn)
    return () => listeners.delete(fn)
  }, [])
}

// Fetch-on-mount + refetch-on-live-event, the app's standard data hook.
export function useData(fetcher, deps = [], liveKinds = ['request', 'state']) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const reload = useCallback(() => {
    fetcher().then(setData).catch(setError)
  }, deps) // eslint-disable-line
  useEffect(() => { reload() }, [reload])
  useLive((kind) => { if (liveKinds.includes(kind)) reload() })
  return { data, error, reload }
}

export const fmtUsd = (n) => {
  n = Number(n || 0)
  if (n === 0) return '$0.00'
  if (n < 0.01) return `$${n.toFixed(4)}`
  if (n < 1000) return `$${n.toFixed(2)}`
  return `$${n.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}
export const fmtNum = (n) => Number(n || 0).toLocaleString('en-US')
export const fmtTime = (ts) => {
  const d = new Date(ts * 1000)
  const today = new Date().toDateString() === d.toDateString()
  return today
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
export const fmtDur = (ms) => {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

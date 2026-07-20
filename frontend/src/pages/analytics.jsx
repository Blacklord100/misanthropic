import { useState } from 'preact/hooks'
import { api, useData, fmtUsd, fmtNum } from '../api'
import { Bars, EmptyState, Segmented, Skeleton } from '../components.jsx'

const pct = (n) => `${(Number(n || 0) * 100).toFixed(1)}%`
const ms = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${n || 0}ms`)
const compact = (n) => {
  n = Number(n || 0)
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

function Tile({ label, value, sub }) {
  return (
    <div class="flex flex-col gap-0.5 px-4 py-3">
      <div class="text-[10.5px] font-medium uppercase tracking-wide text-faint">{label}</div>
      <div class="tnum text-[18px] font-semibold tracking-tight">{value}</div>
      {sub && <div class="text-[11px] text-mute">{sub}</div>}
    </div>
  )
}

function Chart({ title, right, data }) {
  return (
    <div class="panel p-4">
      <div class="mb-2 flex items-baseline justify-between">
        <div class="text-[12px] font-medium text-mute">{title}</div>
        <div class="tnum text-[11.5px] text-faint">{right}</div>
      </div>
      <Bars data={data} height={72} />
    </div>
  )
}

function Breakdown({ title, rows, totalReq }) {
  const entries = Object.entries(rows || {}).sort((a, b) => b[1].requests - a[1].requests)
  if (!entries.length) return null
  return (
    <div class="panel overflow-hidden">
      <div class="border-b border-line px-5 py-3 text-[12.5px] font-medium text-mute">{title}</div>
      <table class="w-full text-[12px]">
        <thead>
          <tr class="border-b border-line text-left text-[10.5px] uppercase tracking-wide text-faint">
            <th class="px-5 py-2 font-medium">Name</th>
            <th class="px-2 py-2 text-right font-medium">Req</th>
            <th class="px-2 py-2 text-right font-medium">Share</th>
            <th class="px-2 py-2 text-right font-medium">Err</th>
            <th class="px-2 py-2 text-right font-medium">Tokens in</th>
            <th class="px-2 py-2 text-right font-medium">Tokens out</th>
            <th class="px-2 py-2 text-right font-medium">Avg</th>
            <th class="px-2 py-2 text-right font-medium">p95</th>
            <th class="px-5 py-2 text-right font-medium">$ dodged</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, r]) => (
            <tr key={name} class="border-b border-line last:border-0">
              <td class="px-5 py-2 font-medium">{name}</td>
              <td class="tnum px-2 py-2 text-right text-mute">{fmtNum(r.requests)}</td>
              <td class="tnum px-2 py-2 text-right text-faint">{pct(r.requests / (totalReq || 1))}</td>
              <td class="tnum px-2 py-2 text-right"
                  style={r.errors ? { color: 'var(--color-err)' } : { color: 'var(--color-faint)' }}>
                {r.errors || 0}
              </td>
              <td class="tnum px-2 py-2 text-right text-mute">{compact(r.input_tokens)}</td>
              <td class="tnum px-2 py-2 text-right text-mute">{compact(r.output_tokens)}</td>
              <td class="tnum px-2 py-2 text-right text-faint">{ms(r.avg_ms)}</td>
              <td class="tnum px-2 py-2 text-right text-faint">{ms(r.p95_ms)}</td>
              <td class="tnum px-5 py-2 text-right" style={{ color: 'var(--color-ok)' }}>{fmtUsd(r.usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Analytics() {
  const [days, setDays] = useState(30)
  const { data } = useData(() => api.analytics(days), [days])

  if (!data) return <div class="mx-auto max-w-4xl px-8 py-8"><Skeleton rows={6} /></div>
  const t = data.totals
  const series = data.series || []
  const label = (d) => d.day.slice(5)

  return (
    <div class="fade-in mx-auto max-w-4xl px-8 py-8">
      <div class="mb-5 flex items-center justify-between">
        <h1 class="text-[17px] font-semibold tracking-tight">Analytics</h1>
        <Segmented value={String(days)} onChange={(v) => setDays(Number(v))}
                    options={[{ value: '7', label: '7d' },
                              { value: '30', label: '30d' },
                              { value: '90', label: '90d' }]} />
      </div>

      {t.requests === 0 ? (
        <EmptyState icon="∿" title="No requests in this window"
                    hint="Widen the range, or point a client at the server." />
      ) : (
        <>
          {/* ---- headline tiles ---- */}
          <div class="panel mb-4 grid grid-cols-4 divide-x divide-line md:grid-cols-8">
            <Tile label="Requests" value={fmtNum(t.requests)} />
            <Tile label="Error rate" value={pct(t.error_rate)} sub={`${fmtNum(t.errors)} failed`} />
            <Tile label="Latency avg" value={ms(t.avg_ms)} sub={`p50 ${ms(t.p50_ms)}`} />
            <Tile label="Latency p95" value={ms(t.p95_ms)} />
            <Tile label="Tokens in" value={compact(t.input_tokens + t.cache_write + t.cache_read)}
                  sub={`${pct(t.cache_read_share)} cached`} />
            <Tile label="Tokens out" value={compact(t.output_tokens)} />
            <Tile label="Streamed" value={pct(t.stream_share)} />
            <Tile label="$ dodged" value={fmtUsd(t.usd)}
                  sub={t.web_requests ? `${fmtNum(t.web_requests)} web searches` : null} />
          </div>

          {/* ---- daily charts ---- */}
          <div class="mb-4 grid gap-4 md:grid-cols-3">
            <Chart title="Requests / day" right={`${fmtNum(t.requests)} total · errors in red`}
                   data={series.map((d) => ({ label: label(d), value: d.requests,
                                              overlay: d.errors,
                                              title: `${d.requests} req, ${d.errors} err` }))} />
            <Chart title="Tokens out / day" right={compact(t.output_tokens)}
                   data={series.map((d) => ({ label: label(d), value: d.output_tokens,
                                              title: compact(d.output_tokens) }))} />
            <Chart title="$ dodged / day" right={fmtUsd(t.usd)}
                   data={series.map((d) => ({ label: label(d), value: d.usd,
                                              title: fmtUsd(d.usd) }))} />
          </div>

          {/* ---- breakdowns ---- */}
          <div class="grid gap-4">
            <Breakdown title="By account" rows={data.by_account} totalReq={t.requests} />
            <Breakdown title="By model" rows={data.by_model} totalReq={t.requests} />
            <Breakdown title="By mode" rows={data.by_mode} totalReq={t.requests} />
          </div>
        </>
      )}
    </div>
  )
}

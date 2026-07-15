import { useState } from 'preact/hooks'
import { api, useData, fmtUsd, fmtNum, fmtTime, fmtDur } from '../api'
import { StatusBadge, ModePill, EmptyState, Skeleton, Segmented, CopyButton } from '../components.jsx'

function Drawer({ row, onClose }) {
  if (!row) return null
  const meta = [
    ['Time', new Date(row.ts * 1000).toLocaleString()],
    ['Key', row.key_label],
    ['Model', row.model],
    ['Mode', `${row.mode}${row.stream ? ' · streamed' : ''}`],
    ['Duration', fmtDur(row.duration_ms)],
    ['Hosted cost dodged', fmtUsd(row.usd)],
  ]
  return (
    <div class="fixed inset-0 z-40 flex justify-end bg-black/30" onMouseDown={onClose}>
      <div
        class="drawer-in flex h-full w-[520px] flex-col border-l border-line bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div class="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div class="flex items-center gap-3">
            <StatusBadge status={row.status} />
            <span class="text-[13px] font-semibold">Request #{row.id}</span>
          </div>
          <button class="btn btn-ghost" onClick={onClose}>Esc</button>
        </div>
        <div class="flex-1 overflow-y-auto p-5">
          <div class="mb-5 grid grid-cols-2 gap-x-6 gap-y-2.5">
            {meta.map(([k, v]) => (
              <div key={k} class="flex flex-col">
                <span class="text-[11px] font-medium uppercase tracking-wide text-faint">{k}</span>
                <span class="tnum mt-0.5 text-[12.5px]">{v}</span>
              </div>
            ))}
          </div>

          {/* token breakdown */}
          <div class="panel mb-5 grid grid-cols-4 divide-x divide-line bg-raised/40">
            {[
              ['Input', row.input_tokens],
              ['Cache write', row.cache_write],
              ['Cache read', row.cache_read],
              ['Output', row.output_tokens],
            ].map(([k, v]) => (
              <div key={k} class="flex flex-col items-center py-3">
                <span class="tnum text-[15px] font-semibold">{fmtNum(v)}</span>
                <span class="text-[10.5px] text-faint">{k}</span>
              </div>
            ))}
          </div>

          {row.error && (
            <section class="mb-5">
              <h3 class="mb-1.5 text-[11px] font-medium uppercase tracking-wide" style={{ color: 'var(--color-err)' }}>Error</h3>
              <pre class="mono whitespace-pre-wrap rounded-md border border-line bg-raised/60 p-3 text-[11.5px] leading-relaxed" style={{ color: 'var(--color-err)' }}>{row.error}</pre>
            </section>
          )}
          {row.prompt_text && (
            <section class="mb-5">
              <div class="mb-1.5 flex items-center justify-between">
                <h3 class="text-[11px] font-medium uppercase tracking-wide text-faint">Prompt</h3>
                <CopyButton text={row.prompt_text} label="Copy" className="btn btn-ghost !h-6 text-[11px]" />
              </div>
              <pre class="whitespace-pre-wrap rounded-md border border-line bg-raised/60 p-3 text-[12px] leading-relaxed">{row.prompt_text}</pre>
            </section>
          )}
          {row.response_text && (
            <section>
              <div class="mb-1.5 flex items-center justify-between">
                <h3 class="text-[11px] font-medium uppercase tracking-wide text-faint">Response</h3>
                <CopyButton text={row.response_text} label="Copy" className="btn btn-ghost !h-6 text-[11px]" />
              </div>
              <pre class="whitespace-pre-wrap rounded-md border border-line bg-raised/60 p-3 text-[12px] leading-relaxed">{row.response_text}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}

export function Requests() {
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(50)
  const [selected, setSelected] = useState(null)
  const { data } = useData(() => api.requests({ limit, status, q }), [limit, status, q])
  const rows = data?.requests

  return (
    <div class="fade-in mx-auto max-w-5xl px-8 py-8">
      <div class="mb-5 flex items-center justify-between gap-4">
        <h1 class="text-[17px] font-semibold tracking-tight">Requests</h1>
        <div class="flex items-center gap-3">
          <input
            class="input w-56"
            placeholder="Search prompt or response…"
            value={q}
            onInput={(e) => setQ(e.target.value)}
          />
          <Segmented
            value={status}
            onChange={setStatus}
            options={[
              { value: '', label: 'All' },
              { value: 'ok', label: 'OK' },
              { value: 'error', label: 'Errors' },
            ]}
          />
        </div>
      </div>

      <div class="panel overflow-hidden">
        {!rows ? (
          <Skeleton rows={8} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon="≡"
            title={q || status ? 'Nothing matches' : 'No requests yet'}
            hint={
              q || status
                ? 'Try widening the filter.'
                : 'History is durable — every request lands here and survives restarts.'
            }
          />
        ) : (
          <table class="w-full text-[12.5px]">
            <thead>
              <tr class="border-b border-line text-left text-[11px] uppercase tracking-wide text-faint">
                <th class="px-5 py-2.5 font-medium">Time</th>
                <th class="px-2 py-2.5 font-medium">Key</th>
                <th class="px-2 py-2.5 font-medium">Model</th>
                <th class="px-2 py-2.5 font-medium">Mode</th>
                <th class="px-2 py-2.5 text-right font-medium">Tokens</th>
                <th class="px-2 py-2.5 text-right font-medium">Cost dodged</th>
                <th class="px-2 py-2.5 text-right font-medium">Time</th>
                <th class="px-5 py-2.5 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.id}
                  class="cursor-default border-b border-line transition-colors last:border-0 hover:bg-raised/50"
                  onClick={() => setSelected(r)}
                >
                  <td class="tnum px-5 py-2.5 text-faint">{fmtTime(r.ts)}</td>
                  <td class="px-2 py-2.5 font-medium">{r.key_label}</td>
                  <td class="mono px-2 py-2.5 text-mute">{r.model}</td>
                  <td class="px-2 py-2.5"><ModePill mode={r.mode} /></td>
                  <td class="tnum px-2 py-2.5 text-right text-mute">
                    {fmtNum(r.input_tokens)} → {fmtNum(r.output_tokens)}
                  </td>
                  <td class="tnum px-2 py-2.5 text-right" style={{ color: 'var(--color-ok)' }}>{fmtUsd(r.usd)}</td>
                  <td class="tnum px-2 py-2.5 text-right text-faint">{fmtDur(r.duration_ms)}</td>
                  <td class="px-5 py-2.5 text-right"><StatusBadge status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows && data?.total > rows.length && (
          <div class="border-t border-line px-5 py-2.5 text-center">
            <button class="btn btn-ghost" onClick={() => setLimit((l) => l + 100)}>
              Load more · {fmtNum(data.total - rows.length)} older
            </button>
          </div>
        )}
      </div>
      <Drawer row={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

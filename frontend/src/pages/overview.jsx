import { api, useData, fmtUsd, fmtNum, fmtTime, fmtDur } from '../api'
import { Sparkline, StatusBadge, ModePill, EmptyState, Skeleton } from '../components.jsx'

function Stat({ label, value, sub }) {
  return (
    <div class="flex flex-col gap-1">
      <div class="text-[11.5px] font-medium uppercase tracking-wide text-faint">{label}</div>
      <div class="tnum text-[22px] font-semibold tracking-tight">{value}</div>
      {sub && <div class="text-[12px] text-mute">{sub}</div>}
    </div>
  )
}

export function Overview() {
  const { data } = useData(() => api.requests({ limit: 8 }))
  const { data: series } = useData(() => api.series(30))

  const savings = data?.savings
  const rows = data?.requests
  const points = series?.series?.map((d) => d.requests) || []
  const monthTotal = series?.series?.reduce((a, d) => a + d.requests, 0) || 0

  return (
    <div class="fade-in mx-auto max-w-4xl px-8 py-8">
      <h1 class="mb-6 text-[17px] font-semibold tracking-tight">Overview</h1>

      {/* ---- savings hero: the whole pitch, front and center ---- */}
      <div class="panel mb-4 p-6">
        {savings ? (
          <div class="flex items-end justify-between gap-6">
            <div>
              <div class="text-[12px] font-medium text-mute">
                You'd have paid the hosted API
              </div>
              <div class="tnum mt-1 text-[40px] font-semibold leading-none tracking-tight" style={{ color: 'var(--color-ok)' }}>
                {fmtUsd(savings.all_time_usd)}
              </div>
              <div class="mt-2 text-[12.5px] text-mute">
                Misanthropic charged <span class="font-medium text-ink">$0.00</span>
                {savings.since && <> · since {new Date(savings.since).toLocaleDateString()}</>}
                {savings.all_time_usd >= 1 && (
                  <>
                    {' · '}
                    <a
                      href="https://paypal.me/Blacklord100"
                      target="_blank"
                      rel="noreferrer"
                      class="transition-colors hover:text-ink"
                      style={{ color: 'var(--color-accent-ink)' }}
                    >
                      send a coffee's worth? ☕
                    </a>
                  </>
                )}
              </div>
            </div>
            <div class="flex gap-10 pb-1">
              <Stat label="This month" value={fmtUsd(savings.month_usd)} sub={`${fmtNum(savings.month_requests)} requests`} />
              <Stat label="All time" value={fmtNum(savings.all_time_requests)} sub="requests served" />
              <Stat
                label="Tokens"
                value={fmtNum(savings.output_tokens)}
                sub={`out · ${fmtNum(savings.input_tokens)} in`}
              />
            </div>
          </div>
        ) : (
          <Skeleton rows={2} />
        )}
      </div>

      {/* ---- 30-day activity ---- */}
      <div class="panel mb-4 p-5">
        <div class="mb-1 flex items-baseline justify-between">
          <div class="text-[12.5px] font-medium text-mute">Requests · last 30 days</div>
          <div class="tnum text-[12.5px] text-faint">{fmtNum(monthTotal)} total</div>
        </div>
        {points.length ? (
          <Sparkline points={points} height={56} />
        ) : (
          <div class="skeleton h-[56px]" />
        )}
      </div>

      {/* ---- recent activity ---- */}
      <div class="panel overflow-hidden">
        <div class="flex items-center justify-between border-b border-line px-5 py-3">
          <div class="text-[12.5px] font-medium text-mute">Recent activity</div>
          <a href="#/requests" class="text-[12px] font-medium" style={{ color: 'var(--color-accent-ink)' }}>
            View all →
          </a>
        </div>
        {!rows ? (
          <Skeleton rows={4} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon="◈"
            title="No requests yet"
            hint="Point any Anthropic SDK at this server and its requests will appear here, live."
          >
            <a href="#/keys" class="btn btn-primary">Connect a project</a>
          </EmptyState>
        ) : (
          <table class="w-full text-[12.5px]">
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} class="border-b border-line last:border-0 hover:bg-raised/50">
                  <td class="tnum px-5 py-2.5 text-faint">{fmtTime(r.ts)}</td>
                  <td class="px-2 py-2.5 font-medium">{r.key_label}</td>
                  <td class="mono px-2 py-2.5 text-mute">{r.model}</td>
                  <td class="px-2 py-2.5"><ModePill mode={r.mode} /></td>
                  <td class="tnum px-2 py-2.5 text-right text-mute">
                    {fmtNum(r.input_tokens)} → {fmtNum(r.output_tokens)}
                  </td>
                  <td class="tnum px-2 py-2.5 text-right text-faint">{fmtDur(r.duration_ms)}</td>
                  <td class="px-5 py-2.5 text-right"><StatusBadge status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

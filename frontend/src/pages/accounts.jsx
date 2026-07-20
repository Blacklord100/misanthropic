import { useEffect, useState } from 'preact/hooks'
import { api, useData, fmtNum, fmtUsd } from '../api'
import { CopyButton, Dot, EmptyState, Modal, Segmented, Skeleton, useToast } from '../components.jsx'

const STATUS = {
  ok: { tone: 'ok', text: 'Operational' },
  limited: { tone: 'warn', text: 'Rate-limited' },
  logged_out: { tone: 'err', text: 'Logged out' },
  disabled: { tone: 'idle', text: 'Disabled' },
  no_binary: { tone: 'err', text: 'CLI not found' },
  unknown: { tone: 'idle', text: 'Not probed' },
  error: { tone: 'warn', text: 'Degraded' },
}

const BACKEND_BADGE = {
  claude: { label: 'Claude', cls: 'bg-raised text-ink' },
  codex: { label: 'Codex', cls: 'bg-raised text-ink' },
}

function Countdown({ seconds }) {
  const [left, setLeft] = useState(seconds)
  useEffect(() => setLeft(seconds), [seconds])
  useEffect(() => {
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [])
  const m = Math.floor(left / 60)
  return <span class="tnum">{m > 0 ? `${m}m ${left % 60}s` : `${left}s`}</span>
}

function LoginHint({ acc }) {
  if (!acc.auth_path) return null
  const cmd = acc.backend === 'codex'
    ? `CODEX_HOME=${acc.auth_path} codex login`
    : `CLAUDE_CONFIG_DIR=${acc.auth_path} claude   # then /login inside`
  return (
    <div class="mt-3 rounded-md border border-line bg-raised/50 p-3">
      <div class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-faint">
        Log this account in (terminal)
      </div>
      <div class="flex items-center gap-2">
        <code class="mono flex-1 overflow-x-auto whitespace-nowrap text-[11.5px]">{cmd}</code>
        <CopyButton text={cmd} label="Copy" className="btn btn-ghost !h-6 text-[11px]" />
      </div>
    </div>
  )
}

function AccountCard({ acc, onChanged }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [showLogin, setShowLogin] = useState(false)
  const st = STATUS[acc.status] || STATUS.unknown
  const stats = acc.stats || {}

  const act = async (fn, msg) => {
    setBusy(true)
    try { await fn(); msg && toast(msg); onChanged() } finally { setBusy(false) }
  }

  return (
    <div class="panel p-4">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <Dot tone={st.tone} pulse={acc.serving} />
            <span class="truncate text-[13.5px] font-semibold">{acc.label}</span>
            <span class={`rounded px-1.5 py-0.5 text-[10.5px] font-medium ${BACKEND_BADGE[acc.backend]?.cls || ''}`}>
              {BACKEND_BADGE[acc.backend]?.label || acc.backend}
            </span>
            {acc.serving && (
              <span class="rounded px-1.5 py-0.5 text-[10.5px] font-semibold"
                    style={{ background: 'color-mix(in oklab, var(--color-ok) 15%, transparent)', color: 'var(--color-ok)' }}>
                Serving
              </span>
            )}
            {acc.pinned && <span class="text-[10.5px] text-faint">📌 pinned</span>}
          </div>
          <div class="mt-1 text-[12px] text-mute">
            {st.text}
            {acc.status === 'limited' && acc.cooldown && (
              <> · retries in <Countdown seconds={acc.cooldown.seconds_left} /></>
            )}
            {acc.detail && acc.status !== 'ok' && (
              <span class="text-faint"> — {acc.detail.slice(0, 80)}</span>
            )}
          </div>
          <div class="tnum mt-2 text-[12px] text-mute">
            {fmtNum(stats.today_requests || 0)} today · {fmtNum(stats.requests || 0)} all-time
            {stats.usd > 0 && <> · {fmtUsd(stats.usd)} dodged</>}
          </div>
        </div>
        <div class="flex shrink-0 flex-col items-end gap-1.5">
          <div class="flex gap-1.5">
            <button class="btn btn-ghost !h-6 text-[11px]" disabled={busy}
                    onClick={() => act(() => api.accountPin(acc.pinned ? null : acc.id),
                                       acc.pinned ? 'Unpinned' : `Pinned ${acc.label}`)}>
              {acc.pinned ? 'Unpin' : 'Pin'}
            </button>
            <button class="btn btn-ghost !h-6 text-[11px]" disabled={busy}
                    onClick={() => act(() => api.accountUpdate(acc.id, { enabled: !acc.enabled }),
                                       acc.enabled ? 'Disabled' : 'Enabled')}>
              {acc.enabled ? 'Disable' : 'Enable'}
            </button>
          </div>
          <div class="flex gap-1.5">
            <button class="btn btn-ghost !h-6 text-[11px]" disabled={busy}
                    onClick={() => act(async () => {
                      const r = await api.accountProbe(acc.id)
                      toast(`${acc.label}: ${(STATUS[r.status] || STATUS.unknown).text}`)
                    })}>
              Verify
            </button>
            <button class="btn btn-ghost !h-6 text-[11px]"
                    onClick={() => setShowLogin((s) => !s)}>
              Login…
            </button>
            {acc.id !== 'claude-default' && (
              <button class="btn btn-ghost !h-6 text-[11px]" disabled={busy}
                      style={{ color: 'var(--color-err)' }}
                      onClick={() => act(() => api.accountDelete(acc.id), 'Account removed')}>
                Remove
              </button>
            )}
          </div>
        </div>
      </div>
      {showLogin && <LoginHint acc={acc} />}
    </div>
  )
}

function AddAccountModal({ open, onClose, onAdded }) {
  const toast = useToast()
  const [backend, setBackend] = useState('claude')
  const [label, setLabel] = useState('')
  const [created, setCreated] = useState(null)
  useEffect(() => { if (open) { setCreated(null); setLabel('') } }, [open])

  const add = async () => {
    const r = await api.accountAdd(label || undefined, backend)
    setCreated(r.account)
    toast('Account added — now log it in')
    onAdded()
  }

  return (
    <Modal open={open} onClose={onClose} title="Add account" width={480}>
      {!created ? (
        <div class="grid gap-4">
          <div>
            <div class="mb-1.5 text-[12px] font-medium">Backend</div>
            <Segmented value={backend} onChange={setBackend}
                       options={[{ value: 'claude', label: 'Claude' },
                                 { value: 'codex', label: 'Codex' }]} />
            <div class="mt-1.5 text-[11.5px] leading-relaxed text-faint">
              {backend === 'claude'
                ? 'Another Claude Pro/Max login. Tools, web search and sessions all work.'
                : 'A ChatGPT login via the Codex CLI. Serves text, images and thinking; tools/web/sessions stay on Claude.'}
            </div>
          </div>
          <div>
            <div class="mb-1.5 text-[12px] font-medium">Label</div>
            <input class="input w-full" placeholder={backend === 'claude' ? 'Claude — work' : 'Codex — personal'}
                   value={label} onInput={(e) => setLabel(e.target.value)} />
          </div>
          <div class="flex justify-end gap-2">
            <button class="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button class="btn btn-primary" onClick={add}>Add account</button>
          </div>
        </div>
      ) : (
        <div class="grid gap-3">
          <div class="text-[12.5px]">
            <span class="font-semibold">{created.label}</span> is registered but not
            logged in yet. Run this in a terminal, complete the login, then hit Verify
            on its card:
          </div>
          <LoginHint acc={{ backend: created.backend, auth_path: created.auth?.path }} />
          <div class="flex justify-end">
            <button class="btn btn-primary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
    </Modal>
  )
}

export function Accounts() {
  const { data, reload } = useData(() => api.accounts(true), [], ['state'])
  const [showAdd, setShowAdd] = useState(false)

  if (!data) return <div class="mx-auto max-w-3xl px-8 py-8"><Skeleton rows={4} /></div>
  const rows = data.accounts || []

  return (
    <div class="fade-in mx-auto max-w-3xl px-8 py-8">
      <div class="mb-5 flex items-center justify-between">
        <h1 class="text-[17px] font-semibold tracking-tight">Accounts</h1>
        <button class="btn btn-primary" onClick={() => setShowAdd(true)}>Add account</button>
      </div>

      <div class="mb-4 text-[12.5px] leading-relaxed text-mute">
        Requests serve from the pinned account first, then priority order. When an
        account hits its usage limit, the next eligible one takes over automatically.
        Tools, web search and session keys always serve from Claude accounts.
      </div>

      {rows.length === 0 ? (
        <EmptyState icon="⇄" title="No accounts" hint="Add a Claude or Codex account to get started." />
      ) : (
        <div class="grid gap-3">
          {rows.map((acc) => <AccountCard key={acc.id} acc={acc} onChanged={reload} />)}
        </div>
      )}

      <AddAccountModal open={showAdd} onClose={() => setShowAdd(false)} onAdded={reload} />
    </div>
  )
}

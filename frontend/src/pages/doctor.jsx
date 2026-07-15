import { useState } from 'preact/hooks'
import { api, useData } from '../api'
import { Dot, Skeleton, useToast } from '../components.jsx'

const CHECK_TONE = (ok) => (ok === true ? 'ok' : ok === false ? 'err' : 'idle')

function Check({ ok, title, detail, children }) {
  return (
    <div class="flex items-start gap-3 border-b border-line px-5 py-4 last:border-0">
      <div class="pt-1"><Dot tone={CHECK_TONE(ok)} /></div>
      <div class="min-w-0 flex-1">
        <div class="text-[13px] font-medium">{title}</div>
        {detail && <div class="mono mt-1 break-all text-[11.5px] leading-relaxed text-mute">{detail}</div>}
      </div>
      {children && <div class="shrink-0">{children}</div>}
    </div>
  )
}

export function Doctor() {
  const toast = useToast()
  const { data, reload } = useData(() => api.doctor(), [], ['state'])
  const [probing, setProbing] = useState(false)
  const [scanning, setScanning] = useState(false)

  if (!data) return <div class="mx-auto max-w-3xl px-8 py-8"><Skeleton rows={4} /></div>

  const b = data.binary
  const login = data.login

  return (
    <div class="fade-in mx-auto max-w-3xl px-8 py-8">
      <div class="mb-5 flex items-center justify-between">
        <h1 class="text-[17px] font-semibold tracking-tight">Doctor</h1>
        <button
          class="btn"
          disabled={scanning}
          onClick={async () => {
            setScanning(true)
            try { await api.rescan(); toast('Environment re-scanned'); reload() } finally { setScanning(false) }
          }}
        >
          {scanning ? 'Scanning…' : 'Re-scan'}
        </button>
      </div>

      <div class="panel">
        <Check
          ok={b.found}
          title={b.found ? 'Claude CLI found' : 'Claude CLI not found'}
          detail={
            b.found
              ? `${b.path} · via ${b.source}`
              : 'Install Claude Code (npm i -g @anthropic-ai/claude-code, or brew install --cask claude-code), then re-scan. If it lives somewhere unusual, set CLAUDE_BIN.'
          }
        />
        <Check
          ok={b.found ? Boolean(data.cli_version) : null}
          title="CLI runs"
          detail={data.cli_version || (b.found ? 'version check pending' : '—')}
        />
        <Check
          ok={login.ok}
          title={
            login.ok === true ? 'Logged in — generation works'
            : login.ok === false ? 'Generation failed'
            : 'Login not verified yet'
          }
          detail={
            login.ok === false
              ? login.detail
              : login.checked_at
                ? `verified ${login.checked_at}`
                : 'Runs one minimal completion through your Claude login — the only honest check.'
          }
        >
          <button
            class="btn"
            disabled={probing || !b.found}
            onClick={async () => {
              setProbing(true)
              try {
                const snap = await api.probe()
                toast(snap.login?.ok ? 'Login verified' : 'Probe failed', snap.login?.ok ? 'ok' : 'err')
                reload()
              } finally { setProbing(false) }
            }}
          >
            {probing ? 'Probing…' : 'Verify now'}
          </button>
        </Check>
      </div>

      <div class="mt-4 text-[11.5px] text-faint">
        State dir: <span class="mono">{data.home}</span>
      </div>
    </div>
  )
}

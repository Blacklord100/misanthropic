import { useState } from 'preact/hooks'
import { api, useData } from '../api'
import { setTheme, themePref } from '../theme.js'
import { Field, Segmented, Skeleton, useToast } from '../components.jsx'

function Row({ title, hint, children }) {
  return (
    <div class="flex items-start justify-between gap-8 border-b border-line px-5 py-4 last:border-0">
      <div class="max-w-sm">
        <div class="text-[13px] font-medium">{title}</div>
        {hint && <div class="mt-1 text-[12px] leading-relaxed text-mute">{hint}</div>}
      </div>
      <div class="shrink-0 pt-0.5">{children}</div>
    </div>
  )
}

export function Settings() {
  const toast = useToast()
  const { data, reload } = useData(api.settings, [], ['state'])
  const [theme, setThemeState] = useState(themePref())

  const save = async (body, msg) => {
    await api.saveSettings(body)
    toast(msg || 'Saved')
    reload()
  }

  if (!data) return <div class="mx-auto max-w-3xl px-8 py-8"><Skeleton rows={4} /></div>

  return (
    <div class="fade-in mx-auto max-w-3xl px-8 py-8">
      <h1 class="mb-5 text-[17px] font-semibold tracking-tight">Settings</h1>

      <div class="panel">
        <Row
          title="Appearance"
          hint="System follows your Mac's light/dark setting live. Stored per browser."
        >
          <Segmented
            value={theme}
            onChange={(v) => { setTheme(v); setThemeState(v); toast(`Appearance: ${v}`) }}
            options={[
              { value: 'system', label: 'System' },
              { value: 'light', label: 'Light' },
              { value: 'dark', label: 'Dark' },
            ]}
          />
        </Row>
        <Row
          title="Default model"
          hint="Used when a request doesn't name one, and as the fallback for unknown model ids."
        >
          <Segmented
            value={data.default_model?.split('[')[0] || 'sonnet'}
            onChange={(v) => save({ default_model: v }, `Default model: ${v}`)}
            options={[
              { value: 'haiku', label: 'Haiku' },
              { value: 'sonnet', label: 'Sonnet' },
              { value: 'opus', label: 'Opus' },
            ]}
          />
        </Row>
        <Row
          title="Web search"
          hint='"Auto" honors each request, exactly like the hosted API. "On" forces web for every request; "Off" is a hard kill-switch.'
        >
          <Segmented
            value={data.web_policy}
            onChange={(v) => save({ web_policy: v }, `Web search: ${v}`)}
            options={[
              { value: 'auto', label: 'Auto' },
              { value: 'on', label: 'On' },
              { value: 'off', label: 'Off' },
            ]}
          />
        </Row>
        <Row
          title="History retention"
          hint="How long request history is kept. Savings totals are never pruned."
        >
          <Segmented
            value={String(data.settings?.retention_days || 0)}
            onChange={(v) => save({ retention_days: Number(v) }, 'Retention updated')}
            options={[
              { value: '30', label: '30d' },
              { value: '90', label: '90d' },
              { value: '0', label: 'Forever' },
            ]}
          />
        </Row>
        <Row
          title="Codex model"
          hint="Model passed to codex runs (-m). Empty uses codex's built-in default. Shown as codex:<model> in the request log — codex never runs the requested Claude model."
        >
          <input
            class="input w-44"
            placeholder="codex default"
            defaultValue={data.settings?.codex_model || ''}
            onBlur={(e) => {
              const v = e.target.value.trim()
              if (v !== (data.settings?.codex_model || '')) save({ codex_model: v }, 'Codex model saved')
            }}
            onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
          />
        </Row>
        <Row
          title="Account failover"
          hint='"Auto" hops to the next eligible account when the serving one hits its usage limit. "Stop" fails the request (529) and waits for the limit to reset. Individual API keys can override this on the Keys page.'
        >
          <Segmented
            value={data.settings?.failover_policy === 'auto' ? 'auto' : 'off'}
            onChange={(v) => save({ failover_policy: v }, `Failover: ${v === 'auto' ? 'auto' : 'stop'}`)}
            options={[
              { value: 'off', label: 'Stop' },
              { value: 'auto', label: 'Auto' },
            ]}
          />
        </Row>
        <Row
          title="Enforce max_tokens"
          hint="Truncate responses at each request's max_tokens. The count is a ~4 chars/token estimate (the CLI exposes no real limit), so this is off by default. stop_sequences are always honored."
        >
          <Segmented
            value={data.settings?.enforce_max_tokens ? 'on' : 'off'}
            onChange={(v) => save({ enforce_max_tokens: v === 'on' }, `Enforce max_tokens: ${v}`)}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'on', label: 'On' },
            ]}
          />
        </Row>
        <Row
          title="Concurrency"
          hint="How many Claude processes may run at once. Extra requests queue briefly, then get the API's 529 so SDKs retry with backoff. Applies immediately."
        >
          <Segmented
            value={String(data.max_concurrency)}
            onChange={(v) => save({ max_concurrency: Number(v) }, `Concurrency: ${v}`)}
            options={[
              { value: '2', label: '2' },
              { value: '4', label: '4' },
              { value: '8', label: '8' },
              { value: '16', label: '16' },
            ]}
          />
        </Row>
      </div>

      <div class="mt-6 text-[11.5px] leading-relaxed text-faint">
        Env vars (MISANTHROPIC_MODEL, MISANTHROPIC_WEB, …) still win at startup — settings changed
        here persist in ~/.misanthropic/settings.json and apply live.
      </div>
    </div>
  )
}

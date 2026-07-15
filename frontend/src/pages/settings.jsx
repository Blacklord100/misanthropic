import { api, useData } from '../api'
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
          title="Concurrency"
          hint={`Up to ${data.max_concurrency} Claude processes run at once; extra requests queue, then get the API's 529 so SDKs retry. Set MISANTHROPIC_MAX_CONCURRENCY to change.`}
        >
          <span class="tnum text-[13px] text-mute">{data.max_concurrency}</span>
        </Row>
      </div>

      <div class="mt-6 text-[11.5px] leading-relaxed text-faint">
        Env vars (MISANTHROPIC_MODEL, MISANTHROPIC_WEB, …) still win at startup — settings changed
        here persist in ~/.misanthropic/settings.json and apply live.
      </div>
    </div>
  )
}

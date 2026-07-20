import { useEffect, useState } from 'preact/hooks'
import { api, useData, fmtUsd, fmtNum } from '../api'
import { EmptyState, Modal, Skeleton, Field, CopyButton, Segmented, useToast } from '../components.jsx'

export function snippetFor(key, base) {
  return {
    python: `from anthropic import Anthropic

client = Anthropic(base_url="${base}", api_key="${key}")`,
    typescript: `import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ baseURL: "${base}", apiKey: "${key}" });`,
    curl: `curl ${base}/v1/messages \\
  -H "x-api-key: ${key}" \\
  -H "content-type: application/json" \\
  -d '{"model":"claude-sonnet-4-6","max_tokens":256,"messages":[{"role":"user","content":"Hello"}]}'`,
    env: `export ANTHROPIC_BASE_URL=${base}
export ANTHROPIC_API_KEY=${key}`,
  }
}

export function ConnectSnippets({ apiKey, base }) {
  const [lang, setLang] = useState('python')
  const snippets = snippetFor(apiKey, base)
  return (
    <div class="overflow-hidden rounded-md border border-line">
      <div class="flex items-center justify-between border-b border-line bg-raised/50 px-2 py-1">
        <div class="flex gap-0.5">
          {Object.keys(snippets).map((l) => (
            <button
              key={l}
              class="rounded px-2 py-1 text-[11.5px] font-medium capitalize"
              style={lang === l ? { color: 'var(--color-ink)' } : { color: 'var(--color-faint)' }}
              onClick={() => setLang(l)}
            >
              {l}
            </button>
          ))}
        </div>
        <CopyButton text={snippets[lang]} label="Copy" className="btn btn-ghost !h-6 text-[11px]" />
      </div>
      <pre class="mono overflow-x-auto p-3 text-[11.5px] leading-relaxed">{snippets[lang]}</pre>
    </div>
  )
}

function NewKeyModal({ open, onClose, base, onCreated }) {
  const [label, setLabel] = useState('')
  const [created, setCreated] = useState(null)
  useEffect(() => { if (open) { setLabel(''); setCreated(null) } }, [open])
  return (
    <Modal open={open} onClose={onClose} title={created ? 'Key created' : 'Create API key'} width={520}>
      {!created ? (
        <form
          onSubmit={async (e) => {
            e.preventDefault()
            const r = await api.createKey(label.trim() || 'default')
            setCreated(r.key)
            onCreated?.()
          }}
        >
          <Field label="Label" hint="Names the project this key connects — it also names its persistent conversation.">
            <input class="input" autoFocus placeholder="my-agent" value={label} onInput={(e) => setLabel(e.target.value)} />
          </Field>
          <div class="mt-4 flex justify-end gap-2">
            <button type="button" class="btn" onClick={onClose}>Cancel</button>
            <button type="submit" class="btn btn-primary">Create key</button>
          </div>
        </form>
      ) : (
        <div class="flex flex-col gap-3">
          <div class="flex items-center gap-2">
            <code class="mono flex-1 truncate rounded-md border border-line bg-raised px-2.5 py-1.5">{created}</code>
            <CopyButton text={created} />
          </div>
          <ConnectSnippets apiKey={created} base={base} />
          <div class="flex justify-end">
            <button class="btn btn-primary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
    </Modal>
  )
}

export function Keys({ state }) {
  const toast = useToast()
  const { data, reload } = useData(api.state)
  const [showNew, setShowNew] = useState(window.location.hash.includes('new=1'))
  const [expanded, setExpanded] = useState(null)
  const keys = data?.keys
  const base = data?.base_url || window.location.origin

  return (
    <div class="fade-in mx-auto max-w-4xl px-8 py-8">
      <div class="mb-5 flex items-center justify-between">
        <div>
          <h1 class="text-[17px] font-semibold tracking-tight">Keys</h1>
          <p class="mt-1 text-[12.5px] text-mute">
            A key authorizes a client <em>and</em> names its conversation — requests under one key share a persistent session.
          </p>
        </div>
        <button class="btn btn-primary" onClick={() => setShowNew(true)}>New key</button>
      </div>

      <div class="panel overflow-hidden">
        {!keys ? (
          <Skeleton rows={3} />
        ) : keys.length === 0 ? (
          <EmptyState
            icon="⌘"
            title="No keys yet"
            hint="Without keys the server runs stateless and open on localhost. Create a key to link a project to its own persistent session."
          >
            <button class="btn btn-primary" onClick={() => setShowNew(true)}>Create your first key</button>
          </EmptyState>
        ) : (
          keys.map((k) => (
            <div key={k.key} class="border-b border-line last:border-0">
              <div
                class="flex cursor-default items-center gap-4 px-5 py-3 transition-colors hover:bg-raised/40"
                onClick={() => setExpanded(expanded === k.key ? null : k.key)}
              >
                <div class="min-w-0 flex-1">
                  <div class="text-[13px] font-medium">{k.label || '(unnamed)'}</div>
                  <div class="mono mt-0.5 truncate text-[11px] text-faint">{k.key}</div>
                </div>
                <div class="tnum text-right text-[12px] text-mute">
                  {fmtNum(k.requests)} req · <span style={{ color: 'var(--color-ok)' }}>{fmtUsd(k.usd)}</span> dodged
                </div>
                <div class="tnum w-20 text-right text-[12px] text-faint">{k.turns} turns</div>
              </div>
              {expanded === k.key && (
                <div class="fade-in flex flex-col gap-3 border-t border-line bg-raised/20 px-5 py-4">
                  <ConnectSnippets apiKey={k.key} base={base} />
                  <div class="flex items-center justify-between gap-4">
                    <div class="max-w-sm text-[12px] leading-relaxed text-mute">
                      <span class="font-medium text-ink">Account failover</span> — whether
                      this project's requests may hop to another account when the serving
                      one hits its usage limit. "Default" follows the Settings policy;
                      for a session key, failing over starts a fresh conversation.
                    </div>
                    <Segmented
                      value={k.failover || 'default'}
                      onChange={async (v) => {
                        await api.keyFailover(k.key, v)
                        toast(`Failover for ${k.label || 'key'}: ${v}`)
                        reload()
                      }}
                      options={[
                        { value: 'default', label: 'Default' },
                        { value: 'on', label: 'On' },
                        { value: 'off', label: 'Off' },
                      ]}
                    />
                  </div>
                  <div class="flex justify-end gap-2">
                    <button
                      class="btn"
                      onClick={async () => { await api.forgetSession(k.key); toast('Session reset — next request starts fresh'); reload() }}
                    >
                      Reset session
                    </button>
                    <button
                      class="btn btn-danger"
                      onClick={async () => {
                        if (!confirm(`Delete key "${k.label}"? Clients using it will get 401s.`)) return
                        await api.deleteKey(k.key)
                        toast('Key deleted', 'err')
                        reload()
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
      <NewKeyModal open={showNew} onClose={() => setShowNew(false)} base={base} onCreated={reload} />
    </div>
  )
}

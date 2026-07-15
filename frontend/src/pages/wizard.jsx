// First-run wizard: find the CLI → verify login → create a key → test drive.
// Every broken state renders as a fix-it step with a retry — never a dead end.
import { useEffect, useState } from 'preact/hooks'
import { api, fmtUsd, fmtNum } from '../api'
import { Dot } from '../components.jsx'
import { ConnectSnippets } from './keys.jsx'

const STEPS = ['Find Claude', 'Verify login', 'Create a key', 'Test drive']

function StepDots({ step }) {
  return (
    <div class="mb-8 flex items-center gap-2">
      {STEPS.map((s, i) => (
        <div key={s} class="flex items-center gap-2">
          <div
            class="flex h-5 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium transition-colors"
            style={
              i === step
                ? { background: 'var(--color-accent)', color: '#fff' }
                : i < step
                  ? { color: 'var(--color-ok)' }
                  : { color: 'var(--color-faint)' }
            }
          >
            {i < step ? '✓' : i + 1} <span class="hidden sm:inline">{s}</span>
          </div>
          {i < STEPS.length - 1 && <div class="h-px w-6" style={{ background: 'var(--color-line-strong)' }} />}
        </div>
      ))}
    </div>
  )
}

export function Wizard({ done }) {
  const [step, setStep] = useState(0)
  const [snap, setSnap] = useState(null)
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState(null)
  const [label, setLabel] = useState('my-project')
  const [test, setTest] = useState(null)
  const base = window.location.origin

  const refresh = () => api.doctor().then(setSnap)
  useEffect(() => { refresh() }, [])

  const finish = async () => {
    await api.saveSettings({ onboarded: true })
    done()
  }

  const binaryOk = snap?.binary?.found
  const loginOk = snap?.login?.ok

  return (
    <div class="flex min-h-screen items-center justify-center px-6">
      <div class="fade-in w-full max-w-xl">
        <div class="mb-8 flex items-center gap-3">
          <img src="/favicon.svg" class="h-8 w-8 rounded-lg" alt="" />
          <div>
            <div class="text-[16px] font-semibold tracking-tight">Welcome to Misanthropic</div>
            <div class="text-[12.5px] text-mute">The Anthropic API, conjured from your own Claude login. Four quick steps.</div>
          </div>
        </div>
        <StepDots step={step} />

        <div class="panel p-6">
          {/* ---- step 1: find claude ---- */}
          {step === 0 && (
            <div class="flex flex-col gap-4">
              {!snap ? (
                <div class="skeleton h-16" />
              ) : binaryOk ? (
                <>
                  <div class="flex items-center gap-2.5 text-[13.5px] font-medium">
                    <Dot tone="ok" /> Found Claude Code
                  </div>
                  <div class="mono rounded-md border border-line bg-raised/60 px-3 py-2 text-[12px] text-mute">
                    {snap.binary.path}
                    <span class="text-faint"> · via {snap.binary.source}</span>
                    {snap.cli_version && <div class="mt-1 text-faint">{snap.cli_version}</div>}
                  </div>
                  <button class="btn btn-primary self-end" onClick={() => setStep(1)}>Continue</button>
                </>
              ) : (
                <>
                  <div class="flex items-center gap-2.5 text-[13.5px] font-medium">
                    <Dot tone="warn" /> Claude Code isn't installed (or wasn't found)
                  </div>
                  <div class="text-[12.5px] leading-relaxed text-mute">
                    Misanthropic fulfills every request through the <span class="mono">claude</span> CLI —
                    your subscription is the auth. Install it, then re-scan:
                  </div>
                  <pre class="mono rounded-md border border-line bg-raised/60 p-3 text-[12px] leading-loose">npm install -g @anthropic-ai/claude-code{'\n'}claude   # then log in when prompted</pre>
                  <div class="flex items-center justify-between">
                    <span class="text-[11.5px] text-faint">Installed somewhere unusual? Set CLAUDE_BIN and restart.</span>
                    <button
                      class="btn btn-primary"
                      disabled={busy}
                      onClick={async () => { setBusy(true); try { setSnap(await api.rescan()) } finally { setBusy(false) } }}
                    >
                      {busy ? 'Scanning…' : 'Re-scan'}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ---- step 2: verify login ---- */}
          {step === 1 && (
            <div class="flex flex-col gap-4">
              <div class="text-[13.5px] font-medium">Verify your Claude login</div>
              <div class="text-[12.5px] leading-relaxed text-mute">
                This runs one tiny real completion through your login — proof the whole chain works.
                It takes a few seconds.
              </div>
              {loginOk === true && (
                <div class="fade-in flex items-center gap-2.5 rounded-md border border-line bg-raised/40 px-3 py-2.5 text-[13px]">
                  <Dot tone="ok" /> Logged in — generation works.
                </div>
              )}
              {loginOk === false && (
                <div class="fade-in rounded-md border border-line bg-raised/40 px-3 py-2.5">
                  <div class="flex items-center gap-2.5 text-[13px]"><Dot tone="err" /> Generation failed</div>
                  <div class="mono mt-1.5 text-[11.5px] text-mute">{snap.login.detail}</div>
                  <div class="mt-2 text-[12px] text-mute">
                    If that reads like a login problem: run <span class="mono">claude</span> in a terminal and sign in, then retry.
                  </div>
                </div>
              )}
              <div class="flex justify-end gap-2">
                {loginOk !== true && (
                  <button
                    class="btn btn-primary"
                    disabled={busy}
                    onClick={async () => { setBusy(true); try { setSnap(await api.probe()) } finally { setBusy(false) } }}
                  >
                    {busy ? 'Verifying…' : loginOk === false ? 'Retry' : 'Verify now'}
                  </button>
                )}
                {loginOk === true && <button class="btn btn-primary" onClick={() => setStep(2)}>Continue</button>}
              </div>
            </div>
          )}

          {/* ---- step 3: create key ---- */}
          {step === 2 && (
            <div class="flex flex-col gap-4">
              <div class="text-[13.5px] font-medium">Create your first key</div>
              <div class="text-[12.5px] leading-relaxed text-mute">
                A key authorizes a client and names its persistent conversation. Drop it anywhere an
                Anthropic key goes.
              </div>
              {!key ? (
                <form
                  class="flex gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    const r = await api.createKey(label.trim() || 'my-project')
                    setKey(r.key)
                  }}
                >
                  <input class="input flex-1" value={label} onInput={(e) => setLabel(e.target.value)} />
                  <button type="submit" class="btn btn-primary">Create</button>
                </form>
              ) : (
                <>
                  <ConnectSnippets apiKey={key} base={base} />
                  <button class="btn btn-primary self-end" onClick={() => setStep(3)}>Continue</button>
                </>
              )}
            </div>
          )}

          {/* ---- step 4: test drive ---- */}
          {step === 3 && (
            <div class="flex flex-col gap-4">
              <div class="text-[13.5px] font-medium">Take it for a spin</div>
              <div class="text-[12.5px] leading-relaxed text-mute">
                One real round-trip through <span class="mono">POST /v1/messages</span> — the same call
                your code will make.
              </div>
              {test?.content && (
                <div class="fade-in rounded-md border border-line bg-raised/40 p-3">
                  <div class="text-[12.5px] leading-relaxed">{test.content?.[0]?.text}</div>
                  <div class="tnum mt-2 border-t border-line pt-2 text-[11.5px] text-faint">
                    {fmtNum(test.usage?.input_tokens)} in → {fmtNum(test.usage?.output_tokens)} out
                    {test._ms != null && <> · {(test._ms / 1000).toFixed(1)}s</>}
                    {test._usd != null && (
                      <> · you just dodged <span style={{ color: 'var(--color-ok)' }}>{fmtUsd(test._usd)}</span></>
                    )}
                  </div>
                </div>
              )}
              {test?.error && (
                <div class="fade-in mono rounded-md border border-line bg-raised/40 p-3 text-[11.5px]" style={{ color: 'var(--color-err)' }}>
                  {test.error.message}
                </div>
              )}
              <div class="flex justify-end gap-2">
                <button
                  class="btn btn-primary"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    const t0 = performance.now()
                    try {
                      const r = await fetch('/v1/messages', {
                        method: 'POST',
                        headers: { 'content-type': 'application/json', 'x-api-key': key || '' },
                        body: JSON.stringify({
                          model: 'claude-sonnet-4-6', max_tokens: 128,
                          messages: [{ role: 'user', content: 'In one short sentence: what just happened here, technically?' }],
                        }),
                      })
                      const j = await r.json()
                      j._ms = Math.round(performance.now() - t0)
                      if (j.usage) j._usd = (j.usage.input_tokens * 3 + j.usage.output_tokens * 15) / 1e6
                      setTest(j)
                    } finally { setBusy(false) }
                  }}
                >
                  {busy ? 'Running…' : test ? 'Run again' : 'Send test request'}
                </button>
                {test?.content && <button class="btn btn-primary" onClick={finish}>Finish →</button>}
              </div>
            </div>
          )}
        </div>

        <button class="mt-4 w-full text-center text-[11.5px] text-faint transition-colors hover:text-mute" onClick={finish}>
          Skip setup — I know what I'm doing
        </button>
      </div>
    </div>
  )
}

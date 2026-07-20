import { useEffect, useMemo, useState } from 'preact/hooks'
import { api, useData } from './api'
import { initTheme, setTheme } from './theme.js'
import { Dot, ToastHost, useToast } from './components.jsx'
import { Overview } from './pages/overview.jsx'
import { Requests } from './pages/requests.jsx'
import { Keys } from './pages/keys.jsx'
import { Accounts } from './pages/accounts.jsx'
import { Settings } from './pages/settings.jsx'
import { Wizard } from './pages/wizard.jsx'

// The old Doctor page lives inside Accounts now (its Environment panel);
// #/doctor deep links land there too.
const PAGES = [
  { path: 'overview', label: 'Overview', icon: '◈', el: Overview },
  { path: 'requests', label: 'Requests', icon: '≡', el: Requests },
  { path: 'keys', label: 'Keys', icon: '⌘', el: Keys },
  { path: 'accounts', label: 'Accounts', icon: '⇄', el: Accounts, aliases: ['doctor'] },
  { path: 'settings', label: 'Settings', icon: '⚙', el: Settings },
]

function useRoute() {
  const read = () => window.location.hash.replace(/^#\/?/, '') || 'overview'
  const [route, setRoute] = useState(read)
  useEffect(() => {
    const fn = () => setRoute(read())
    window.addEventListener('hashchange', fn)
    return () => window.removeEventListener('hashchange', fn)
  }, [])
  return [route, (r) => (window.location.hash = `#/${r}`)]
}

const STATUS_TONE = { ok: 'ok', unknown: 'ok', no_binary: 'warn', not_logged_in: 'err', error: 'warn' }
const STATUS_TEXT = {
  ok: 'Operational',
  unknown: 'Operational',
  no_binary: 'Claude CLI not found',
  not_logged_in: 'Not logged in',
  error: 'Degraded',
}

function CommandPalette({ open, onClose, go }) {
  const toast = useToast()
  const [q, setQ] = useState('')
  const actions = useMemo(
    () => [
      ...PAGES.map((p) => ({ label: `Go to ${p.label}`, run: () => go(p.path) })),
      {
        label: 'Copy base URL',
        run: () => { navigator.clipboard.writeText(window.location.origin); toast('Base URL copied') },
      },
      { label: 'Create API key', run: () => go('keys?new=1') },
      { label: 'Re-scan environment', run: async () => { await api.rescan(); toast('Environment re-scanned') } },
      {
        label: 'Toggle light/dark',
        run: () => {
          setTheme(document.documentElement.classList.contains('light') ? 'dark' : 'light')
        },
      },
    ],
    [],
  )
  const hits = actions.filter((a) => a.label.toLowerCase().includes(q.toLowerCase()))
  const [sel, setSel] = useState(0)
  useEffect(() => setSel(0), [q, open])
  useEffect(() => { if (open) setQ('') }, [open])
  if (!open) return null
  return (
    <div class="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[16vh]" onMouseDown={onClose}>
      <div class="panel fade-in w-[460px] overflow-hidden shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <input
          autoFocus
          class="w-full border-b border-line bg-transparent px-4 py-3 text-[13.5px] outline-none placeholder:text-faint"
          placeholder="Type a command…"
          value={q}
          onInput={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') setSel((s) => Math.min(s + 1, hits.length - 1))
            else if (e.key === 'ArrowUp') setSel((s) => Math.max(s - 1, 0))
            else if (e.key === 'Enter' && hits[sel]) { hits[sel].run(); onClose() }
            else if (e.key === 'Escape') onClose()
          }}
        />
        <div class="max-h-72 overflow-y-auto p-1.5">
          {hits.map((a, i) => (
            <button
              key={a.label}
              class="flex w-full items-center rounded-md px-2.5 py-2 text-left text-[13px]"
              style={i === sel ? { background: 'var(--color-raised)' } : {}}
              onMouseEnter={() => setSel(i)}
              onClick={() => { a.run(); onClose() }}
            >
              {a.label}
            </button>
          ))}
          {!hits.length && <div class="px-3 py-6 text-center text-[12.5px] text-faint">No matching commands</div>}
        </div>
      </div>
    </div>
  )
}

export function App() {
  const [route, go] = useRoute()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const { data: state } = useData(api.state)
  const { data: health } = useData(api.health, [], ['request', 'state'])

  useEffect(() => {
    initTheme()
    const fn = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setPaletteOpen((v) => !v) }
    }
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [])

  const routeBase = route.split('?')[0]
  const Page = (PAGES.find((p) => p.path === routeBase || p.aliases?.includes(routeBase))
    || PAGES[0]).el
  const claudeStatus = health?.claude || 'unknown'

  if (state?.first_run && routeBase !== 'wizard') {
    return (
      <ToastHost>
        <Wizard done={() => { window.location.hash = '#/overview'; window.location.reload() }} />
      </ToastHost>
    )
  }

  return (
    <ToastHost>
      <div class="flex h-screen">
        {/* ---- sidebar ---- */}
        <aside class="flex w-[210px] shrink-0 flex-col border-r border-line px-3 py-4">
          <div class="mb-6 flex items-center gap-2.5 px-2">
            <img src="/favicon.svg" class="h-6 w-6 rounded-md" alt="" />
            <div class="text-[13.5px] font-semibold tracking-tight">Misanthropic</div>
          </div>
          <nav class="flex flex-col gap-0.5">
            {PAGES.map((p) => (
              <a
                key={p.path}
                href={`#/${p.path}`}
                class="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] font-medium transition-colors"
                style={
                  routeBase === p.path
                    ? { background: 'var(--color-raised)', color: 'var(--color-ink)' }
                    : { color: 'var(--color-mute)' }
                }
              >
                <span class="w-4 text-center text-[11px] opacity-70">{p.icon}</span>
                {p.label}
                {p.path === 'accounts' && claudeStatus !== 'ok' && claudeStatus !== 'unknown' && (
                  <span class="ml-auto"><Dot tone={STATUS_TONE[claudeStatus] || 'warn'} pulse /></span>
                )}
              </a>
            ))}
          </nav>
          <button
            class="mt-3 flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] text-faint transition-colors hover:text-mute"
            onClick={() => setPaletteOpen(true)}
          >
            Search… <span class="kbd ml-auto">⌘K</span>
          </button>
          <div class="mt-auto flex flex-col gap-2 px-2">
            <a
              href="https://paypal.me/Blacklord100"
              target="_blank"
              rel="noreferrer"
              class="flex items-center gap-2 rounded-md py-1 text-[11.5px] text-faint transition-colors hover:text-ink"
              title="If Misanthropic saves you money, buy Mithuran a coffee"
            >
              ☕ Buy me a coffee
            </a>
            <div class="flex items-center gap-2 text-[11.5px] text-mute">
              <Dot tone={STATUS_TONE[claudeStatus] || 'idle'} />
              {STATUS_TEXT[claudeStatus] || 'Checking…'}
            </div>
            <div class="text-[11px] text-faint">v{state?.version || '…'} · {state?.mode || ''}</div>
          </div>
        </aside>

        {/* ---- content ---- */}
        <main class="min-w-0 flex-1 overflow-y-auto">
          <Page state={state} health={health} />
        </main>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} go={go} />
    </ToastHost>
  )
}

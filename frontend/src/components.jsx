// Shared primitives: sparkline, badges, toasts, modal, empty states.
import { useEffect, useRef, useState } from 'preact/hooks'
import { createContext } from 'preact'
import { useContext } from 'preact/hooks'

export function Sparkline({ points, height = 44, stroke = 'var(--color-accent)' }) {
  if (!points?.length) return null
  const w = 100, h = 100
  const max = Math.max(...points, 1)
  const step = w / Math.max(points.length - 1, 1)
  const ys = points.map((p) => h - (p / max) * (h - 8) - 4)
  const line = ys.map((y, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(2)},${y.toFixed(2)}`).join(' ')
  const area = `${line} L${w},${h} L0,${h} Z`
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height }}>
      <path d={area} fill={stroke} opacity="0.08" />
      <path d={line} fill="none" stroke={stroke} stroke-width="1.6" vector-effect="non-scaling-stroke" />
    </svg>
  )
}

export function StatusBadge({ status }) {
  const ok = status === 200
  return (
    <span
      class="tnum inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{
        color: ok ? 'var(--color-ok)' : 'var(--color-err)',
        background: ok ? 'rgba(76,195,138,0.09)' : 'rgba(229,72,77,0.09)',
      }}
    >
      {status ?? '…'}
    </span>
  )
}

export function ModePill({ mode }) {
  return (
    <span class="rounded border border-line px-1.5 py-px text-[10.5px] text-mute">{mode}</span>
  )
}

export function Dot({ tone = 'ok', pulse = false }) {
  const colors = { ok: 'var(--color-ok)', warn: 'var(--color-warn)', err: 'var(--color-err)', idle: 'var(--color-faint)' }
  return (
    <span
      class={`inline-block h-[7px] w-[7px] rounded-full ${pulse ? 'pulse-dot' : ''}`}
      style={{ background: colors[tone] }}
    />
  )
}

export function EmptyState({ icon, title, hint, children }) {
  return (
    <div class="fade-in flex flex-col items-center justify-center gap-2 py-16 text-center">
      <div class="text-2xl opacity-40">{icon}</div>
      <div class="text-[14px] font-medium">{title}</div>
      {hint && <div class="max-w-sm text-[12.5px] leading-relaxed text-mute">{hint}</div>}
      {children && <div class="mt-3">{children}</div>}
    </div>
  )
}

export function Modal({ open, onClose, title, children, width = 440 }) {
  useEffect(() => {
    if (!open) return
    const fn = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [open])
  if (!open) return null
  return (
    <div class="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[18vh]" onMouseDown={onClose}>
      <div
        class="panel fade-in shadow-2xl"
        style={{ width }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {title && (
          <div class="border-b border-line px-4 py-3 text-[13px] font-semibold">{title}</div>
        )}
        <div class="p-4">{children}</div>
      </div>
    </div>
  )
}

// ---- toasts ------------------------------------------------------------------
const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastHost({ children }) {
  const [toasts, setToasts] = useState([])
  const push = (msg, tone = 'ok') => {
    const id = Math.random()
    setToasts((t) => [...t, { id, msg, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600)
  }
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div class="pointer-events-none fixed bottom-5 left-1/2 z-[100] flex -translate-x-1/2 flex-col items-center gap-2">
        {toasts.map((t) => (
          <div key={t.id} class="panel fade-in flex items-center gap-2 px-3.5 py-2 text-[12.5px] shadow-xl">
            <Dot tone={t.tone} /> {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function CopyButton({ text, label = 'Copy', className = 'btn' }) {
  const toast = useToast()
  return (
    <button
      class={className}
      onClick={() => {
        navigator.clipboard.writeText(text)
        toast('Copied to clipboard')
      }}
    >
      {label}
    </button>
  )
}

export function Skeleton({ rows = 3 }) {
  return (
    <div class="flex flex-col gap-2 p-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} class="skeleton h-8" style={{ opacity: 1 - i * 0.18 }} />
      ))}
    </div>
  )
}

export function Field({ label, hint, children }) {
  return (
    <label class="flex flex-col gap-1.5">
      <span class="text-[12px] font-medium text-mute">{label}</span>
      {children}
      {hint && <span class="text-[11.5px] leading-relaxed text-faint">{hint}</span>}
    </label>
  )
}

export function Segmented({ options, value, onChange }) {
  return (
    <div class="inline-flex rounded-md border border-line bg-raised p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          class="rounded-[5px] px-2.5 py-1 text-[12px] font-medium transition-colors"
          style={
            value === o.value
              ? { background: 'var(--color-panel)', color: 'var(--color-ink)', boxShadow: '0 1px 2px rgba(0,0,0,.3)' }
              : { color: 'var(--color-mute)' }
          }
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

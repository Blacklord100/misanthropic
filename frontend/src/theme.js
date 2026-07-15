// Appearance preference: 'system' | 'light' | 'dark', stored per-browser.
// 'system' tracks the OS live via prefers-color-scheme.

const mq = window.matchMedia('(prefers-color-scheme: light)')

export function themePref() {
  const t = localStorage.getItem('theme')
  return t === 'light' || t === 'dark' ? t : 'system'
}

function apply() {
  const pref = themePref()
  const light = pref === 'light' || (pref === 'system' && mq.matches)
  document.documentElement.classList.toggle('light', light)
}

export function setTheme(pref) {
  if (pref === 'system') localStorage.removeItem('theme')
  else localStorage.setItem('theme', pref)
  apply()
}

export function initTheme() {
  apply()
  mq.addEventListener('change', apply) // live OS switches in 'system' mode
}

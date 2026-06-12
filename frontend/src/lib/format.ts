export function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  return `${cents}¢`
}

export function formatPnL(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  const dollars = Math.abs(cents) / 100
  const sign = cents >= 0 ? '+' : '-'
  return `${sign}$${dollars.toFixed(2)}`
}

export function pnlClass(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return 'text-slate-500'
  if (cents > 0) return 'text-emerald-400'
  if (cents < 0) return 'text-red-400'
  return 'text-slate-400'
}

export function formatPct(n: number): string {
  return `${(n * 100).toFixed(0)}%`
}

export function formatScore(n: number): string {
  return n.toFixed(3)
}

export function formatDateTime(isoStr: string): string {
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return isoStr
  }
}

export function formatDate(isoStr: string): string {
  try {
    const d = new Date(isoStr)
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return isoStr
  }
}

export function formatInning(half: string, number: number): string {
  return `${half === 'T' ? '▲' : '▼'}${number}`
}

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function signalVariant(type: string): BadgeVariant {
  const t = type.toLowerCase()
  if (t.includes('blowup') || t.includes('fade_overreaction')) return 'orange'
  if (t === 'midgame_blowup_fade') return 'orange'
  if (t.includes('stability')) return 'blue'
  if (t.includes('pace_fade')) return 'purple'
  if (t.includes('lagging')) return 'cyan'
  if (t.includes('trap')) return 'red'
  if (t.includes('no_chase') || t.includes('too_early')) return 'yellow'
  if (t.includes('exit')) return 'slate'
  if (t.includes('ladder')) return 'indigo'
  return 'gray'
}

export function actionVariant(action: string | null): BadgeVariant {
  if (action === 'paper_entry') return 'green'
  if (action === 'candidate') return 'blue'
  return 'gray'
}

export function statusVariant(status: string): BadgeVariant {
  if (status === 'open') return 'blue'
  if (status === 'settled') return 'green'
  if (status === 'exited') return 'yellow'
  return 'gray'
}

export type BadgeVariant =
  | 'blue'
  | 'green'
  | 'orange'
  | 'red'
  | 'purple'
  | 'cyan'
  | 'yellow'
  | 'indigo'
  | 'slate'
  | 'gray'

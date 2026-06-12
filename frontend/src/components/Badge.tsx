import type { BadgeVariant } from '../lib/format'

const variantClasses: Record<BadgeVariant, string> = {
  blue:   'bg-blue-950 text-blue-300 border border-blue-800/40',
  green:  'bg-emerald-950 text-emerald-300 border border-emerald-800/40',
  orange: 'bg-orange-950 text-orange-300 border border-orange-800/40',
  red:    'bg-red-950 text-red-300 border border-red-800/40',
  purple: 'bg-purple-950 text-purple-300 border border-purple-800/40',
  cyan:   'bg-cyan-950 text-cyan-300 border border-cyan-800/40',
  yellow: 'bg-yellow-950 text-yellow-300 border border-yellow-800/40',
  indigo: 'bg-indigo-950 text-indigo-300 border border-indigo-800/40',
  slate:  'bg-slate-800 text-slate-300 border border-slate-600/40',
  gray:   'bg-slate-900 text-slate-400 border border-slate-700/30',
}

const dotColors: Record<BadgeVariant, string> = {
  blue:   'bg-blue-400',
  green:  'bg-emerald-400',
  orange: 'bg-orange-400',
  red:    'bg-red-400',
  purple: 'bg-purple-400',
  cyan:   'bg-cyan-400',
  yellow: 'bg-yellow-400',
  indigo: 'bg-indigo-400',
  slate:  'bg-slate-400',
  gray:   'bg-slate-500',
}

interface BadgeProps {
  label: string
  variant: BadgeVariant
  dot?: boolean
  size?: 'xs' | 'sm'
}

export function Badge({ label, variant, dot, size = 'xs' }: BadgeProps) {
  const sz = size === 'xs'
    ? 'px-1.5 py-0.5 text-[10px]'
    : 'px-2 py-1 text-xs'

  return (
    <span className={`inline-flex items-center gap-1 rounded font-medium ${sz} ${variantClasses[variant]}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColors[variant]}`} aria-hidden />}
      {label}
    </span>
  )
}

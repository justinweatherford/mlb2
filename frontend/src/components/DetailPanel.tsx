import { useEffect } from 'react'

interface DetailPanelProps {
  isOpen: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}

export function DetailPanel({ isOpen, onClose, title, children }: DetailPanelProps) {
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  return (
    <>
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 transition-opacity"
          onClick={onClose}
          aria-hidden
        />
      )}
      <div
        role="dialog"
        aria-modal
        aria-label={title}
        className={`
          fixed right-0 top-0 h-full w-[420px] bg-[#0c1120] border-l border-[#1a2540]
          z-50 overflow-y-auto flex flex-col
          transition-transform duration-200 ease-out
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}
        `}
      >
        <div className="flex items-center justify-between px-4 py-3.5 border-b border-[#1a2540] flex-shrink-0">
          <span className="text-sm font-semibold text-slate-200">{title}</span>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 transition-colors p-1 rounded hover:bg-slate-800"
            aria-label="Close panel"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="flex-1 p-4 space-y-5 overflow-y-auto">
          {children}
        </div>
      </div>
    </>
  )
}

interface DetailRowProps {
  label: string
  value: React.ReactNode
  mono?: boolean
}

export function DetailRow({ label, value, mono }: DetailRowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">{label}</div>
      <div className={`text-sm text-slate-300 ${mono ? 'font-mono text-[12px]' : ''}`}>{value ?? '—'}</div>
    </div>
  )
}

interface SectionProps {
  title: string
  children: React.ReactNode
}

export function DetailSection({ title, children }: SectionProps) {
  return (
    <div>
      <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-2.5 flex items-center gap-2">
        <span className="flex-1 border-t border-[#1a2540]" />
        {title}
        <span className="flex-1 border-t border-[#1a2540]" />
      </div>
      {children}
    </div>
  )
}

interface ConfBarProps {
  value: number
}

export function ConfidenceBar({ value }: ConfBarProps) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? '#22c55e' : pct >= 50 ? '#3b82f6' : '#f59e0b'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 progress-bar">
        <div className="progress-bar-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono text-slate-300 w-8 text-right">{pct}%</span>
    </div>
  )
}

interface StatCardProps {
  title: string
  value: string | number
  subtitle?: string
  valueClass?: string
  mono?: boolean
}

export function StatCard({ title, value, subtitle, valueClass, mono }: StatCardProps) {
  return (
    <div className="card px-4 py-3 flex flex-col gap-1">
      <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">{title}</div>
      <div
        className={`text-xl font-bold leading-none ${valueClass ?? 'text-slate-100'} ${
          mono ? 'font-mono' : ''
        }`}
      >
        {value}
      </div>
      {subtitle && <div className="text-[11px] text-slate-500">{subtitle}</div>}
    </div>
  )
}

export function CardSkeleton() {
  return (
    <div className="card px-4 py-3 animate-pulse flex flex-col gap-2">
      <div className="h-2 w-16 bg-slate-800 rounded" />
      <div className="h-6 w-12 bg-slate-700 rounded" />
    </div>
  )
}

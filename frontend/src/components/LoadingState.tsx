interface LoadingStateProps {
  rows?: number
  cols?: number
}

export function LoadingState({ rows = 6, cols = 5 }: LoadingStateProps) {
  return (
    <div className="animate-pulse">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4 px-3 py-2.5 border-b border-[#0f1a2e]">
          {Array.from({ length: cols }).map((_, j) => (
            <div
              key={j}
              className="h-3 rounded bg-slate-800"
              style={{ width: `${60 + Math.random() * 80}px` }}
            />
          ))}
        </div>
      ))}
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

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-16">
      <div
        className="w-8 h-8 rounded-full border-2 border-slate-700 border-t-blue-500 animate-spin"
        role="status"
        aria-label="Loading"
      />
    </div>
  )
}

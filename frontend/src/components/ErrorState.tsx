interface ErrorStateProps {
  message?: string
  retry?: () => void
}

export function ErrorState({ message, retry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <svg className="w-10 h-10 text-red-500/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
      </svg>
      <div className="text-sm text-slate-400 text-center max-w-xs">
        {message ?? 'Failed to load data. Check that the FastAPI server is running on port 8000.'}
      </div>
      {retry && (
        <button onClick={retry} className="btn-ghost text-xs mt-1">
          Try again
        </button>
      )}
    </div>
  )
}

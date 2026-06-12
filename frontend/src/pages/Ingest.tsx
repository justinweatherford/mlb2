import { useState, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { IngestResponse, DryRunResponse, SignalLogEntry } from '../types/api'
import { Badge } from '../components/Badge'
import { formatCents, signalVariant } from '../lib/format'

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`w-4 h-4 text-slate-500 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
      fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
    </svg>
  )
}

function MiniSpinner() {
  return (
    <div
      className="w-3.5 h-3.5 rounded-full border-2 border-slate-600 border-t-blue-400 animate-spin flex-shrink-0"
      aria-hidden
    />
  )
}

type BannerVariant = 'success' | 'warn' | 'info' | 'error'

const BANNER_STYLES: Record<BannerVariant, { wrap: string; iconPath: string; iconClass: string; textClass: string }> = {
  success: {
    wrap: 'bg-emerald-950/60 border-emerald-800/40',
    iconClass: 'text-emerald-400',
    textClass: 'text-emerald-200',
    iconPath: 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  },
  warn: {
    wrap: 'bg-amber-950/50 border-amber-800/40',
    iconClass: 'text-amber-400',
    textClass: 'text-amber-200',
    iconPath: 'M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z',
  },
  info: {
    wrap: 'bg-blue-950/50 border-blue-800/40',
    iconClass: 'text-blue-400',
    textClass: 'text-blue-200',
    iconPath: 'm11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z',
  },
  error: {
    wrap: 'bg-red-950/50 border-red-900/40',
    iconClass: 'text-red-400',
    textClass: 'text-red-200',
    iconPath: 'M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z',
  },
}

function StatusBanner({ variant, children }: { variant: BannerVariant; children: React.ReactNode }) {
  const s = BANNER_STYLES[variant]
  return (
    <div className={`rounded-lg border px-4 py-3 flex items-start gap-3 ${s.wrap}`} role="alert">
      <svg className={`w-4 h-4 mt-0.5 flex-shrink-0 ${s.iconClass}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d={s.iconPath} />
      </svg>
      <p className={`text-sm leading-snug ${s.textClass}`}>{children}</p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Metric card
// ---------------------------------------------------------------------------

function MetCard({ label, value, colorClass }: { label: string; value: number; colorClass: string }) {
  return (
    <div className="card px-3 py-2.5 text-center">
      <div className={`text-xl font-bold font-mono leading-none ${colorClass}`}>{value}</div>
      <div className="text-[10px] text-slate-600 mt-1">{label}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Signal log — categorized
// ---------------------------------------------------------------------------

const CATEGORY_ORDER = ['paper_entry', 'exit_check', 'trap', 'skipped', 'no_entry'] as const
type Category = typeof CATEGORY_ORDER[number]

const CATEGORY_META: Record<Category, { label: string; headerClass: string; rowClass: string }> = {
  paper_entry: {
    label: 'Paper Entries',
    headerClass: 'text-emerald-400 bg-emerald-950/40 border-emerald-900/40',
    rowClass: 'bg-emerald-950/10',
  },
  exit_check: {
    label: 'Exit Checks',
    headerClass: 'text-blue-400 bg-blue-950/40 border-blue-900/40',
    rowClass: '',
  },
  trap: {
    label: 'Traps / No Bets',
    headerClass: 'text-amber-400 bg-amber-950/30 border-amber-900/40',
    rowClass: 'bg-amber-950/5',
  },
  skipped: {
    label: 'Skipped',
    headerClass: 'text-slate-500 bg-slate-900/40 border-slate-800/40',
    rowClass: '',
  },
  no_entry: {
    label: 'No Entry',
    headerClass: 'text-slate-600 bg-[#070b14] border-slate-900/40',
    rowClass: '',
  },
}

function SignalResultCell({ entry }: { entry: SignalLogEntry }) {
  if (entry.category === 'paper_entry') {
    return <Badge label={`Paper Entry #${entry.pos_id}`} variant="green" dot />
  }
  if (entry.category === 'exit_check') {
    return (
      <span className="text-xs text-blue-400 font-mono">
        {entry.pos_id != null ? `Pos #${entry.pos_id}` : 'No position linked'}
      </span>
    )
  }
  if (entry.category === 'trap') {
    return <Badge label="Trap" variant="orange" />
  }
  if (entry.blocked_by) {
    return <span className="text-[11px] text-slate-500 font-mono">{entry.blocked_by}</span>
  }
  return <span className="text-slate-700">—</span>
}

function SignalLogTable({ entries }: { entries: SignalLogEntry[] }) {
  const grouped = new Map<Category, SignalLogEntry[]>()
  for (const cat of CATEGORY_ORDER) grouped.set(cat, [])
  for (const entry of entries) {
    const cat = (entry.category as Category) in CATEGORY_META ? (entry.category as Category) : 'no_entry'
    grouped.get(cat)!.push(entry)
  }

  const rows: React.ReactNode[] = []
  for (const cat of CATEGORY_ORDER) {
    const items = grouped.get(cat)!
    if (items.length === 0) continue
    const meta = CATEGORY_META[cat]
    rows.push(
      <tr key={`hdr-${cat}`}>
        <td
          colSpan={7}
          className={`px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider border-y ${meta.headerClass}`}
        >
          {meta.label} ({items.length})
        </td>
      </tr>
    )
    for (const [i, s] of items.entries()) {
      rows.push(
        <tr key={`${cat}-${i}`} className={meta.rowClass}>
          <td className="font-mono font-medium text-slate-200">{s.game_id}</td>
          <td>
            {cat === 'exit_check' ? (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-950/60 text-blue-300 border border-blue-800/40">
                Exit Check
              </span>
            ) : (
              <Badge
                label={s.signal_type.replace(/_/g, ' ')}
                variant={signalVariant(s.signal_type)}
              />
            )}
          </td>
          <td>
            {s.signal_subtype ? (
              <Badge
                label={s.signal_subtype.replace(/_/g, ' ')}
                variant={signalVariant(s.signal_subtype)}
              />
            ) : (
              <span className="text-slate-700">—</span>
            )}
          </td>
          <td>
            {s.side !== '—' ? (
              <span
                className={`font-mono font-semibold text-xs ${
                  s.side === 'YES' ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                {s.side}
              </span>
            ) : (
              <span className="text-slate-700">—</span>
            )}
          </td>
          <td className="font-mono text-slate-300">
            {s.price > 0 ? formatCents(s.price) : <span className="text-slate-700">—</span>}
          </td>
          <td className="font-mono text-[11px] text-slate-400">
            {Math.round(s.conf * 100)}%
          </td>
          <td>
            <SignalResultCell entry={s} />
          </td>
        </tr>
      )
    }
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Game</th>
            <th>Signal</th>
            <th>Subtype</th>
            <th>Side</th>
            <th>Price</th>
            <th>Conf</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Preview panel
// ---------------------------------------------------------------------------

function PreviewMetCard({ label, value, colorClass }: { label: string; value: number | string; colorClass: string }) {
  return (
    <div className="rounded-lg border border-indigo-900/40 bg-indigo-950/20 px-3 py-2.5 text-center">
      <div className={`text-xl font-bold font-mono leading-none ${colorClass}`}>{value}</div>
      <div className="text-[10px] text-slate-600 mt-1">{label}</div>
    </div>
  )
}

function PreviewPanel({
  preview,
  onRunIngest,
  isPending,
}: {
  preview: DryRunResponse
  onRunIngest: () => void
  isPending: boolean
}) {
  const [showSampleFailures, setShowSampleFailures] = useState(false)

  return (
    <div className="rounded-lg border border-indigo-800/40 bg-indigo-950/10 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-indigo-900/40 flex items-center justify-between">
        <span className="text-sm font-semibold text-indigo-300 flex items-center gap-2">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.964-7.178Z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
          </svg>
          Dry Run Preview
          <span className="text-[10px] font-normal text-indigo-500">(no DB changes)</span>
        </span>
        {preview.is_large && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-950/60 text-amber-300 border border-amber-800/40">
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            Large transcript — {preview.chunks_split} chunks
          </span>
        )}
      </div>

      {/* Metric rows */}
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <PreviewMetCard label="Chunks"     value={preview.chunks_split}     colorClass="text-slate-200" />
          <PreviewMetCard label="New"        value={preview.new_chunks}        colorClass={preview.new_chunks > 0 ? 'text-indigo-300' : 'text-slate-600'} />
          <PreviewMetCard label="Duplicates" value={preview.existing_duplicates} colorClass={preview.existing_duplicates > 0 ? 'text-amber-400' : 'text-slate-600'} />
          <PreviewMetCard label="Failures"   value={preview.parse_failures}   colorClass={preview.parse_failures > 0 ? 'text-red-400' : 'text-slate-600'} />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <PreviewMetCard
            label="Unique Games"
            value={preview.unique_games.length}
            colorClass={preview.unique_games.length > 0 ? 'text-blue-300' : 'text-slate-600'}
          />
          <PreviewMetCard label="Parsed"     value={preview.parsed}           colorClass={preview.parsed > 0 ? 'text-emerald-300' : 'text-slate-600'} />
          <PreviewMetCard label="Est. Signals" value={preview.generated_signal_candidates} colorClass={preview.generated_signal_candidates > 0 ? 'text-blue-400' : 'text-slate-600'} />
          <PreviewMetCard label="Est. Entries" value={preview.estimated_paper_entries}     colorClass={preview.estimated_paper_entries > 0 ? 'text-emerald-400' : 'text-slate-600'} />
        </div>

        {preview.unique_games.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {preview.unique_games.map((g) => (
              <span key={g} className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#0a0f1c] border border-[#1a2540] text-slate-400">
                {g}
              </span>
            ))}
          </div>
        )}

        {preview.sample_failures.length > 0 && (
          <div className="rounded border border-[#1a2540] overflow-hidden">
            <button
              type="button"
              className="w-full px-3 py-2 flex items-center justify-between text-left hover:bg-[#0f1829] transition-colors"
              onClick={() => setShowSampleFailures((v) => !v)}
            >
              <span className="text-xs text-slate-400 font-medium">
                Sample failures ({preview.sample_failures.length})
              </span>
              <ChevronIcon open={showSampleFailures} />
            </button>
            {showSampleFailures && (
              <div className="border-t border-[#1a2540] divide-y divide-[#0f1a2e]">
                {preview.sample_failures.map((f, i) => (
                  <div key={i} className="px-3 py-2">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[10px] font-mono text-slate-600">#{f.index}</span>
                      <span className="text-[10px] text-red-400">{f.reason}</span>
                    </div>
                    <div className="font-mono text-[10px] text-slate-500 bg-[#0a0f1c] rounded px-2 py-1 truncate">
                      {f.snippet}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* CTA */}
        <div className="flex items-center justify-between pt-1">
          {preview.is_large && (
            <p className="text-xs text-amber-400/80">
              This will write {preview.new_chunks} new chunks to the DB.
            </p>
          )}
          <div className="ml-auto">
            <button
              type="button"
              className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={onRunIngest}
              disabled={isPending}
            >
              {isPending ? (
                <>
                  <MiniSpinner />
                  Processing…
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z" />
                  </svg>
                  Run Ingest Now
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function Ingest() {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<'realistic' | 'optimistic'>('realistic')
  const [showLog, setShowLog] = useState(false)
  const [showFailures, setShowFailures] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const mutation = useMutation<IngestResponse, Error>({
    mutationFn: () => api.ingest({ text, mode }),
    onSuccess: () => {
      setConfirming(false)
      queryClient.invalidateQueries()
    },
  })

  const previewMutation = useMutation<DryRunResponse, Error>({
    mutationFn: () => api.ingestPreview({ text, mode }),
  })

  const chunkEstimate = text
    ? text.split('⚾').filter((p) => /\d+-\d+/.test(p)).length
    : 0

  const isLargeTranscript = chunkEstimate > 500

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    const reader = new FileReader()
    reader.onload = (ev) => setText((ev.target?.result as string) ?? '')
    reader.readAsText(file)
    e.target.value = ''
  }

  function handleClear() {
    setText('')
    setFileName(null)
    setConfirming(false)
    mutation.reset()
    previewMutation.reset()
  }

  function handleRunIngest() {
    if (isLargeTranscript && !confirming) {
      setConfirming(true)
    } else {
      mutation.mutate()
    }
  }

  const result = mutation.data

  const isAlreadyIngested =
    !!result &&
    result.chunks_split > 0 &&
    result.skipped_duplicates === result.chunks_split &&
    result.persisted_signal_events === 0

  const isPartialSuccess =
    !!result && result.parsed > 0 && result.failures.length > 0

  const isFullSuccess =
    !!result && result.parsed > 0 && result.failures.length === 0

  const isParsedButNoSignals =
    !!result &&
    result.parsed > 0 &&
    result.generated_signal_candidates === 0 &&
    result.failures.length === 0

  const isNothingParsed =
    !!result && result.parsed === 0 && result.failures.length > 0

  return (
    <div className="p-6 max-w-[960px]">
      <div className="page-header flex-wrap gap-3">
        <h1 className="page-title">Ingest Transcript</h1>
        <div className="flex items-center gap-2 ml-auto">
          <label htmlFor="ingest-mode" className="text-xs text-slate-500 select-none">Mode</label>
          <select
            id="ingest-mode"
            className="field-input"
            value={mode}
            onChange={(e) => setMode(e.target.value as 'realistic' | 'optimistic')}
            disabled={mutation.isPending}
          >
            <option value="realistic">Realistic</option>
            <option value="optimistic">Optimistic</option>
          </select>
        </div>
      </div>

      {/* Input card */}
      <div className="card p-4 space-y-3">
        <div>
          <label htmlFor="ingest-text" className="text-xs font-medium text-slate-500 uppercase tracking-wider">
            Transcript
          </label>
          <textarea
            id="ingest-text"
            className="mt-1.5 w-full rounded-md bg-[#060911] border border-[#1a2540] text-slate-300 text-xs leading-relaxed placeholder-slate-700 px-3 py-2.5 resize-y focus:outline-none focus:border-blue-700/60 focus:ring-1 focus:ring-blue-800/40 transition-colors"
            style={{ minHeight: '240px', fontFamily: 'ui-monospace, "Cascadia Code", monospace' }}
            placeholder={"Paste raw Discord transcript here (⚾-separated messages)…\n\nExample:\n⚾ Run: WSH @ SF 2-0 (T3) — ...\nTotals 8.5: YES 60¢ / NO 40¢"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={mutation.isPending}
            spellCheck={false}
            aria-label="Transcript text"
          />
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-slate-400 border border-[#1a2540] bg-[#0c1120] hover:border-[#2a3a60] hover:text-slate-300 cursor-pointer transition-colors select-none">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
            </svg>
            {fileName ?? 'Browse .txt'}
            <input
              ref={fileRef}
              type="file"
              accept=".txt"
              className="hidden"
              onChange={handleFileChange}
              disabled={mutation.isPending || previewMutation.isPending}
              aria-label="Upload transcript file"
            />
          </label>

          {(text || fileName) && !mutation.isPending && !previewMutation.isPending && (
            <button
              type="button"
              className="text-xs text-slate-600 hover:text-slate-400 transition-colors"
              onClick={handleClear}
            >
              Clear
            </button>
          )}

          <div className="flex-1" />

          {isLargeTranscript && !mutation.isPending && !previewMutation.isPending && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-950/60 text-amber-300 border border-amber-800/40">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              Large — {chunkEstimate} chunks
            </span>
          )}

          {chunkEstimate > 0 && !isLargeTranscript && !mutation.isPending && !previewMutation.isPending && !result && (
            <span className="text-xs text-slate-600 font-mono tabular-nums">
              ~{chunkEstimate} chunk{chunkEstimate !== 1 ? 's' : ''} to process
            </span>
          )}

          {/* Preview button */}
          <button
            type="button"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-indigo-400 border border-indigo-800/50 bg-indigo-950/20 hover:bg-indigo-950/40 hover:border-indigo-700/60 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={() => previewMutation.mutate()}
            disabled={!text.trim() || mutation.isPending || previewMutation.isPending}
          >
            {previewMutation.isPending ? (
              <>
                <MiniSpinner />
                Previewing…
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.964-7.178Z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                </svg>
                Preview
              </>
            )}
          </button>

          {/* Run Ingest / Confirm large */}
          {confirming ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-amber-300">
                Ingest {chunkEstimate} chunks?
              </span>
              <button
                type="button"
                className="px-3 py-1.5 rounded-md text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-40"
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending}
              >
                {mutation.isPending ? 'Processing…' : 'Confirm'}
              </button>
              <button
                type="button"
                className="px-3 py-1.5 rounded-md text-xs font-medium text-slate-400 border border-[#1a2540] hover:border-[#2a3a60] transition-colors"
                onClick={() => setConfirming(false)}
                disabled={mutation.isPending}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={handleRunIngest}
              disabled={!text.trim() || mutation.isPending || previewMutation.isPending}
              aria-busy={mutation.isPending}
            >
              {mutation.isPending ? (
                <>
                  <MiniSpinner />
                  Processing…
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z" />
                  </svg>
                  Run Ingest
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Preview panel — appears when dry run completes, before real ingest results */}
      {(previewMutation.data || previewMutation.isError) && (
        <div className="mt-5 space-y-4">
          {previewMutation.isError && (
            <StatusBanner variant="error">
              Preview failed: {previewMutation.error?.message ?? 'check the FastAPI server log.'}
            </StatusBanner>
          )}
          {previewMutation.data && (
            <PreviewPanel
              preview={previewMutation.data}
              onRunIngest={handleRunIngest}
              isPending={mutation.isPending}
            />
          )}
        </div>
      )}

      {/* Results section */}
      {(result || mutation.isError) && (
        <div className="mt-5 space-y-4">
          {/* Status banner */}
          {mutation.isError && (
            <StatusBanner variant="error">
              {mutation.error.message ?? 'Ingest failed — check the FastAPI server log.'}
            </StatusBanner>
          )}
          {result && isAlreadyIngested && (
            <StatusBanner variant="info">
              Already ingested — every chunk in this transcript matches existing DB rows. No new data was added.
            </StatusBanner>
          )}
          {result && isNothingParsed && (
            <StatusBanner variant="error">
              Nothing parsed — {result.failures.length} chunk{result.failures.length !== 1 ? 's' : ''} failed. See the Unrecognised Chunks section below.
            </StatusBanner>
          )}
          {result && isPartialSuccess && (
            <StatusBanner variant="warn">
              Partially ingested — {result.parsed} messages parsed
              {result.paper_entries_opened > 0
                ? `, ${result.paper_entries_opened} paper ${result.paper_entries_opened === 1 ? 'entry' : 'entries'} opened`
                : ', no entries fired'}
              , {result.failures.length} chunk{result.failures.length !== 1 ? 's' : ''} unrecognised. All pages refreshed.
            </StatusBanner>
          )}
          {result && isFullSuccess && !isParsedButNoSignals && (
            <StatusBanner variant="success">
              Ingested — {result.parsed} messages, {result.persisted_signal_events} stored signal{result.persisted_signal_events !== 1 ? 's' : ''}
              {result.paper_entries_opened > 0
                ? `, ${result.paper_entries_opened} paper ${result.paper_entries_opened === 1 ? 'entry' : 'entries'} opened`
                : ' (no entries fired)'
              }.{result.generated_signal_candidates > result.persisted_signal_events
                ? ` (${result.generated_signal_candidates} generated, ${result.persisted_signal_events} stored)`
                : ''
              } All pages refreshed.
            </StatusBanner>
          )}
          {result && isParsedButNoSignals && (
            <StatusBanner variant="success">
              Ingested — {result.parsed} messages parsed, no signals fired this session. All pages refreshed.
            </StatusBanner>
          )}

          {/* Metric cards — two rows */}
          {result && (
            <>
              {/* Row 1: Parsing stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <MetCard label="Chunks Split"    value={result.chunks_split}          colorClass="text-slate-100" />
                <MetCard label="Parsed"          value={result.parsed}                colorClass="text-emerald-300" />
                <MetCard label="Dup Skips"       value={result.skipped_duplicates}    colorClass={result.skipped_duplicates > 0 ? 'text-amber-400' : 'text-slate-600'} />
                <MetCard label="Parse Failures"  value={result.skipped_parse_failures} colorClass={result.skipped_parse_failures > 0 ? 'text-red-400' : 'text-slate-600'} />
              </div>

              {/* Row 2: Signal stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <MetCard label="Generated"      value={result.generated_signal_candidates} colorClass={result.generated_signal_candidates > 0 ? 'text-blue-400' : 'text-slate-600'} />
                <MetCard label="Stored Signals" value={result.persisted_signal_events}     colorClass={result.persisted_signal_events > 0 ? 'text-emerald-400' : 'text-slate-600'} />
                <MetCard label="Paper Entries"  value={result.paper_entries_opened}        colorClass={result.paper_entries_opened > 0 ? 'text-emerald-300' : 'text-slate-600'} />
                <MetCard label="Pace-Fade Rows" value={result.pace_fade_rows}              colorClass={result.pace_fade_rows > 0 ? 'text-purple-300' : 'text-slate-600'} />
              </div>

              {/* Signal log expander */}
              {result.signal_log.length > 0 && (
                <div className="card overflow-hidden">
                  <button
                    type="button"
                    className="w-full px-4 py-3 flex items-center justify-between hover:bg-[#0f1829] transition-colors"
                    onClick={() => setShowLog((v) => !v)}
                    aria-expanded={showLog}
                  >
                    <span className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                      Signal Log
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-blue-950 text-blue-300 text-[10px] font-mono border border-blue-800/40">
                        {result.signal_log.length} generated
                      </span>
                      {result.persisted_signal_events > 0 && (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-emerald-950 text-emerald-300 text-[10px] font-mono border border-emerald-800/40">
                          {result.persisted_signal_events} stored
                        </span>
                      )}
                    </span>
                    <ChevronIcon open={showLog} />
                  </button>
                  {showLog && (
                    <div className="border-t border-[#1a2540]">
                      <SignalLogTable entries={result.signal_log} />
                    </div>
                  )}
                </div>
              )}

              {/* Failures expander */}
              {result.failures.length > 0 && (
                <div className="card overflow-hidden">
                  <button
                    type="button"
                    className="w-full px-4 py-3 flex items-center justify-between hover:bg-[#0f1829] transition-colors"
                    onClick={() => setShowFailures((v) => !v)}
                    aria-expanded={showFailures}
                  >
                    <span className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                      Unrecognised Chunks
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-950 text-amber-300 text-[10px] font-mono border border-amber-800/40">
                        {result.failures.length}
                      </span>
                    </span>
                    <ChevronIcon open={showFailures} />
                  </button>
                  {showFailures && (
                    <div className="border-t border-[#1a2540] divide-y divide-[#0f1a2e]">
                      {result.failures.map((f, i) => (
                        <div key={i} className="px-4 py-3">
                          <div className="flex items-center gap-3 mb-1">
                            <span className="text-[10px] font-mono text-slate-600">#{f.index}</span>
                            <span className="text-[10px] text-red-400">{f.reason}</span>
                          </div>
                          <div className="font-mono text-[11px] text-slate-500 bg-[#0a0f1c] rounded px-2 py-1.5 truncate">
                            {f.snippet}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

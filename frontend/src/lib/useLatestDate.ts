import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { today } from './format'

/**
 * Resolves to the latest date that has raw messages in the DB.
 * Falls back to today() if the DB is empty.
 * Returns null while the fetch is in flight so callers can gate dependent queries.
 */
export function useLatestDate(): { latestDate: string | null; isLoading: boolean } {
  const q = useQuery({
    queryKey: ['latest-date'],
    queryFn: api.latestDate,
    staleTime: 60_000,
    retry: 1,
  })
  const latestDate = q.isSuccess ? (q.data.latest_date ?? today()) : null
  return { latestDate, isLoading: q.isLoading }
}

import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '@/lib/api'
import { listHistory, type HistoryEntry } from '@/lib/history'
import type { BulkResult } from '@/lib/types'

export type HistoryItem = HistoryEntry & {
  result: BulkResult | null
  missing: boolean // the server no longer knows this batch
  error: string | null
}

// Loads the current state of every batch this browser has started.
export function useHistoryBatches() {
  const [items, setItems] = useState<HistoryItem[] | null>(null)

  const load = useCallback(async () => {
    const entries = listHistory()
    const loaded = await Promise.all(
      entries.map(async (entry): Promise<HistoryItem> => {
        try {
          return { ...entry, result: await api.getBatch(entry.id), missing: false, error: null }
        } catch (err) {
          const e = err as ApiError
          return { ...entry, result: null, missing: e.status === 404, error: e.message }
        }
      }),
    )
    setItems(loaded)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return { items, reload: load }
}

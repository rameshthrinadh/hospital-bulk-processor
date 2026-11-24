// The backend has no "list batches" endpoint, so this browser remembers the
// batches it started. The server may have forgotten them (in-memory store).

export type HistoryEntry = { id: string; fileName: string; uploadedAt: string }

const KEY = 'hospital-bulk:batches'

export function listHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

function save(entries: HistoryEntry[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries))
  } catch {
    // Storage unavailable (private mode); history just won't persist.
  }
}

export function addToHistory(id: string, fileName: string) {
  save([{ id, fileName, uploadedAt: new Date().toISOString() }, ...listHistory()])
}

export function removeFromHistory(id: string) {
  save(listHistory().filter((e) => e.id !== id))
}

export function clearHistory() {
  save([])
}

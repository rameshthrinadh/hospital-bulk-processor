import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { BatchStatusBadge } from '@/components/StatusBadge'
import { Badge } from '@/components/ui/badge'
import type { HistoryItem } from '@/hooks/useHistoryBatches'
import { formatDate } from '@/lib/utils'

function Counts({ item }: { item: HistoryItem }) {
  if (!item.result) return <span className="text-zinc-400">—</span>
  const { total_hospitals, processed_hospitals, failed_hospitals } = item.result
  return (
    <span className="tabular-nums text-zinc-600">
      {total_hospitals} total · <span className="text-emerald-700">{processed_hospitals} ok</span>
      {failed_hospitals > 0 && <> · <span className="text-red-700">{failed_hospitals} failed</span></>}
    </span>
  )
}

function Status({ item }: { item: HistoryItem }) {
  if (item.result) return <BatchStatusBadge status={item.result.status} />
  return <Badge variant="neutral">{item.missing ? 'No longer available' : 'Unreachable'}</Badge>
}

// One row per batch, as links to the batch page. Works at every width.
export function BatchList({ items, actions }: { items: HistoryItem[]; actions?: (item: HistoryItem) => ReactNode }) {
  return (
    <ul className="divide-y divide-zinc-100">
      {items.map((item) => (
        <li key={item.id} className="flex items-center gap-2 pr-3">
          <Link
            to={`/batches/${item.id}`}
            className="flex min-w-0 flex-1 flex-col gap-2 px-5 py-4 transition-colors hover:bg-zinc-50/60 focus-visible:bg-zinc-50 focus-visible:outline-none sm:flex-row sm:items-center sm:gap-6"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{item.fileName}</p>
              <p className="truncate font-mono text-xs text-zinc-500">{item.id}</p>
            </div>
            <div className="text-sm sm:w-56"><Counts item={item} /></div>
            <div className="text-xs text-zinc-500 sm:w-36">{formatDate(item.result?.created_at ?? item.uploadedAt)}</div>
            <div className="sm:w-40"><Status item={item} /></div>
          </Link>
          {actions?.(item)}
          <ChevronRight className="hidden size-4 text-zinc-400 sm:block" />
        </li>
      ))}
    </ul>
  )
}

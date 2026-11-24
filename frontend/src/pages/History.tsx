import { History as HistoryIcon, RefreshCw, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { BatchList } from '@/components/BatchList'
import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { useHistoryBatches } from '@/hooks/useHistoryBatches'
import { clearHistory, removeFromHistory } from '@/lib/history'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function OpenById() {
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  const valid = UUID.test(value.trim())

  function submit(event: FormEvent) {
    event.preventDefault()
    if (valid) navigate(`/batches/${value.trim()}`)
  }

  return (
    <form onSubmit={submit} className="flex gap-2">
      <label htmlFor="batch-id" className="sr-only">Batch ID</label>
      <input
        id="batch-id"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Open a batch by ID"
        className="h-9 w-full min-w-0 rounded-lg border border-zinc-200 bg-white px-3 font-mono text-xs shadow-xs placeholder:font-sans placeholder:text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 sm:w-80"
      />
      <Button type="submit" variant="outline" disabled={!valid}>Open</Button>
    </form>
  )
}

export function History() {
  const { items, reload } = useHistoryBatches()

  return (
    <>
      <PageHeader
        title="Batch history"
        description="Batches started from this browser. The server keeps batches in memory, so older ones may no longer be available."
        action={<OpenById />}
      />

      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-zinc-100 px-5 py-3">
          <p className="text-sm text-zinc-500">{items ? `${items.length} batch${items.length === 1 ? '' : 'es'}` : 'Loading…'}</p>
          <div className="flex gap-1">
            <Button variant="ghost" size="sm" onClick={reload}><RefreshCw /> Refresh</Button>
            {items && items.length > 0 && (
              <ConfirmDialog
                title="Clear history?"
                description="This removes the list from this browser only. Batches and hospitals on the server are not affected."
                confirmLabel="Clear history"
                onConfirm={() => { clearHistory(); reload() }}
                trigger={(open) => <Button variant="ghost" size="sm" onClick={open}><Trash2 /> Clear</Button>}
              />
            )}
          </div>
        </div>

        {items === null ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={HistoryIcon}
            title="No batches yet"
            description="Batches you upload will appear here."
            action={<Link to="/upload" className={buttonVariants({ variant: 'outline' })}>Upload CSV</Link>}
          />
        ) : (
          <BatchList
            items={items}
            actions={(item) => (
              <ConfirmDialog
                title="Remove from history?"
                description="The batch is only removed from this list. Nothing changes on the server."
                confirmLabel="Remove"
                onConfirm={() => { removeFromHistory(item.id); reload() }}
                trigger={(open) => (
                  <Button variant="ghost" size="icon" onClick={open} aria-label={`Remove ${item.fileName} from history`}>
                    <Trash2 className="text-zinc-400" />
                  </Button>
                )}
              />
            )}
          />
        )}
      </Card>
    </>
  )
}

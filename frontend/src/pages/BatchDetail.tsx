import { ArrowLeft, Filter as FilterIcon, Loader2, RotateCcw, SearchX } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { Alert } from '@/components/Alert'
import { CopyButton } from '@/components/CopyButton'
import { EmptyState } from '@/components/EmptyState'
import { RowsTable } from '@/components/RowsTable'
import { StatCard } from '@/components/StatCard'
import { BatchStatusBadge } from '@/components/StatusBadge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { useBatch } from '@/hooks/useBatch'
import { api, ApiError } from '@/lib/api'
import { ACTIVE_STATUSES, RESUMABLE_STATUSES } from '@/lib/status'
import type { BulkResult, HospitalResult } from '@/lib/types'
import { cn, formatDate } from '@/lib/utils'

const filters = {
  all: { label: 'All', match: () => true },
  success: { label: 'Successful', match: (r: HospitalResult) => r.hospital_id !== null },
  failed: { label: 'Failed', match: (r: HospitalResult) => r.status === 'failed' },
  pending: { label: 'Pending', match: (r: HospitalResult) => r.status === 'pending' },
}
type Filter = keyof typeof filters

function progressLabel(batch: BulkResult) {
  const done = batch.processed_hospitals + batch.failed_hospitals
  if (batch.status === 'pending') return 'Waiting to start…'
  if (batch.status === 'activating') return 'Activating batch…'
  if (batch.status === 'processing' && done === batch.total_hospitals) return 'Verifying batch with the directory…'
  if (batch.status === 'processing') return `Creating hospitals · ${done} of ${batch.total_hospitals}`
  return `${done} of ${batch.total_hospitals} rows finished`
}

function ProgressCard({ batch }: { batch: BulkResult }) {
  const running = ACTIVE_STATUSES.includes(batch.status)
  const done = batch.processed_hospitals + batch.failed_hospitals
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-4 text-sm">
          <span className="flex items-center gap-2 font-medium">
            {running && <Loader2 className="size-4 animate-spin text-accent" />}
            {progressLabel(batch)}
          </span>
          <span className="tabular-nums text-zinc-500">{batch.processing_time_seconds.toFixed(1)}s</span>
        </div>
        <Progress value={(done / batch.total_hospitals) * 100} />
        {running && (
          <p className="text-xs text-zinc-500">
            Still running on the server. You can leave this page; the batch will keep going.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ResumeCard({ onResume, resuming, error }: { onResume: () => void; resuming: boolean; error: string | null }) {
  return (
    <Card className="border-amber-200">
      <CardHeader>
        <CardTitle>Resume this batch</CardTitle>
        <CardDescription>
          Only rows that were not created are retried. Hospitals that already exist are never sent again.
          The batch is then verified and activated.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Alert tone="error" title="Could not resume">{error}</Alert>}
        <Button onClick={onResume} disabled={resuming}>
          {resuming ? <Loader2 className="animate-spin" /> : <RotateCcw />}
          {resuming ? 'Starting…' : 'Resume processing'}
        </Button>
      </CardContent>
    </Card>
  )
}

export function BatchDetail() {
  const { id = '' } = useParams()
  const { batch, setBatch, connection, notFound, reconnect } = useBatch(id)
  const [filter, setFilter] = useState<Filter>('all')
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState<string | null>(null)

  async function resume() {
    setResuming(true)
    setResumeError(null)
    try {
      setBatch(await api.resumeBatch(id))
      reconnect() // the previous socket closed when the last run ended
    } catch (err) {
      setResumeError((err as ApiError).message)
    } finally {
      setResuming(false)
    }
  }

  const back = (
    <Link to="/history" className={cn(buttonVariants({ variant: 'ghost', size: 'sm' }), '-ml-3 mb-4')}>
      <ArrowLeft /> All batches
    </Link>
  )

  if (notFound) {
    return (
      <>
        {back}
        <Card>
          <EmptyState
            icon={SearchX}
            title="Batch not available"
            description="The server doesn't know this batch. Batches are kept in memory, so they disappear when the server restarts. Hospitals already created remain in the directory."
            action={<Link to="/upload" className={buttonVariants({ variant: 'outline' })}>Upload a new CSV</Link>}
          />
        </Card>
      </>
    )
  }

  const offline = connection === 'reconnecting' && (
    <Alert tone="error" title="Connection lost. Reconnecting…">
      Live updates are paused. If the batch was running, it keeps going on the server.
    </Alert>
  )

  if (!batch) {
    return (
      <>
        {back}
        <div className="space-y-6">
          {offline}
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-24" />
          <Skeleton className="h-64" />
        </div>
      </>
    )
  }

  const running = ACTIVE_STATUSES.includes(batch.status)
  const rows = batch.hospitals.filter(filters[filter].match)

  return (
    <>
      {back}
      <div className="mb-8 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Batch</h1>
            <BatchStatusBadge status={batch.status} />
            {connection === 'live' && running && (
              <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                <span className="size-1.5 animate-pulse rounded-full bg-emerald-500" /> Live
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-1">
            <p className="truncate font-mono text-xs text-zinc-500">{batch.batch_id}</p>
            <CopyButton text={batch.batch_id} label="Copy batch ID" />
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-zinc-500 sm:text-right">
          <dt>Uploaded</dt>
          <dd className="text-zinc-700">{formatDate(batch.created_at)}</dd>
          <dt>Last run finished</dt>
          <dd className="text-zinc-700">{running ? 'In progress' : formatDate(batch.updated_at)}</dd>
        </dl>
      </div>

      <div className="space-y-6">
        {offline}
        <ProgressCard batch={batch} />

        {batch.error && !running && <Alert tone="error" title={batch.error} />}
        {batch.status === 'completed' && (
          <Alert tone="success" title="Batch completed and activated">
            All {batch.total_hospitals} hospitals were created, verified and activated.
          </Alert>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Total records" value={batch.total_hospitals} />
          <StatCard label="Successful" value={batch.processed_hospitals} />
          <StatCard label="Failed" value={batch.failed_hospitals} />
        </div>

        {RESUMABLE_STATUSES.includes(batch.status) && (
          <ResumeCard onResume={resume} resuming={resuming} error={resumeError} />
        )}

        <Card className="overflow-hidden">
          <CardHeader className="flex-col gap-3 pb-4 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle>Hospitals</CardTitle>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter rows">
              {(Object.keys(filters) as Filter[]).map((key) => (
                <Button
                  key={key}
                  size="sm"
                  variant={filter === key ? 'outline' : 'ghost'}
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                >
                  {filters[key].label}
                  <span className="tabular-nums text-zinc-400">{batch.hospitals.filter(filters[key].match).length}</span>
                </Button>
              ))}
            </div>
          </CardHeader>
          <div className="border-t border-zinc-100">
            {rows.length ? (
              <RowsTable rows={rows} />
            ) : (
              <EmptyState icon={FilterIcon} title="No rows match this filter" description="Try a different filter." />
            )}
          </div>
        </Card>
      </div>
    </>
  )
}

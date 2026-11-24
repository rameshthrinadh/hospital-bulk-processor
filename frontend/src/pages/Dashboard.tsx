import { Inbox, RefreshCw, Upload } from 'lucide-react'
import { Link } from 'react-router'
import { BatchList } from '@/components/BatchList'
import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { StatCard } from '@/components/StatCard'
import { buttonVariants, Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useHealth, type Health } from '@/hooks/useHealth'
import { useHistoryBatches } from '@/hooks/useHistoryBatches'
import { cn } from '@/lib/utils'

const healthText: Record<Health, { label: string; dot: string }> = {
  checking: { label: 'Checking…', dot: 'bg-zinc-300' },
  online: { label: 'Online', dot: 'bg-emerald-500' },
  offline: { label: 'Unreachable', dot: 'bg-red-500' },
}

function HealthCard() {
  const { health, check } = useHealth()
  const { label, dot } = healthText[health]
  return (
    <Card>
      <CardContent className="flex items-center gap-4">
        <span className={cn('size-2.5 rounded-full', dot, health === 'online' && 'ring-4 ring-emerald-100')} />
        <div className="flex-1">
          <p className="text-sm font-medium">Processing service · {label}</p>
          <p className="text-xs text-zinc-500">
            {health === 'offline'
              ? 'The backend is not responding. Uploads will fail until it is back.'
              : 'The service contacts the Hospital Directory API while batches run; failures appear on each batch.'}
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={check} aria-label="Check again" disabled={health === 'checking'}>
          <RefreshCw />
        </Button>
      </CardContent>
    </Card>
  )
}

export function Dashboard() {
  const { items } = useHistoryBatches()
  const available = items?.filter((i) => i.result) ?? []
  const sum = (pick: (r: NonNullable<(typeof available)[number]['result']>) => number) =>
    items ? available.reduce((total, i) => total + pick(i.result!), 0) : null
  const hint = items ? `Across ${available.length} batch${available.length === 1 ? '' : 'es'} from this browser` : undefined

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Bulk-create hospitals in the Hospital Directory from a CSV file."
        action={
          <Link to="/upload" className={buttonVariants()}>
            <Upload /> Upload CSV
          </Link>
        }
      />

      <div className="space-y-6">
        <HealthCard />

        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Total records" value={sum((r) => r.total_hospitals)} hint={hint} />
          <StatCard label="Successful" value={sum((r) => r.processed_hospitals)} hint="Created on the directory" />
          <StatCard label="Failed" value={sum((r) => r.failed_hospitals)} hint="Could not be created" />
        </div>

        <Card>
          <CardHeader className="flex-row items-center justify-between pb-4">
            <div>
              <CardTitle>Recent batches</CardTitle>
              <CardDescription>The latest uploads from this browser.</CardDescription>
            </div>
            {items && items.length > 0 && (
              <Link to="/history" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>View all</Link>
            )}
          </CardHeader>
          <div className="border-t border-zinc-100">
            {items === null ? (
              <div className="space-y-3 p-5">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : items.length === 0 ? (
              <EmptyState
                icon={Inbox}
                title="No batches yet"
                description="Upload a CSV of up to 20 hospitals to create your first batch."
                action={<Link to="/upload" className={buttonVariants({ variant: 'outline' })}>Upload CSV</Link>}
              />
            ) : (
              <BatchList items={items.slice(0, 5)} />
            )}
          </div>
        </Card>
      </div>
    </>
  )
}

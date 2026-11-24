import type { ReactNode } from 'react'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function StatCard({ label, value, hint }: { label: string; value: ReactNode | null; hint?: string }) {
  return (
    <Card className="p-5">
      <p className="text-sm text-zinc-500">{label}</p>
      {value === null ? (
        <Skeleton className="mt-2 h-8 w-16" />
      ) : (
        <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
      )}
      {hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>}
    </Card>
  )
}

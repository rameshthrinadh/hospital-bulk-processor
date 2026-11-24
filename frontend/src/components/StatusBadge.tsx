import { Badge } from '@/components/ui/badge'
import { batchStatusInfo, rowStatusInfo } from '@/lib/status'
import type { BatchStatus, RowStatus } from '@/lib/types'

export function BatchStatusBadge({ status }: { status: BatchStatus }) {
  const { label, variant } = batchStatusInfo[status]
  return <Badge variant={variant}>{label}</Badge>
}

export function RowStatusBadge({ status }: { status: RowStatus }) {
  const { label, variant } = rowStatusInfo[status]
  return <Badge variant={variant}>{label}</Badge>
}

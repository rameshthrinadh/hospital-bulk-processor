import type { BadgeVariant } from '@/components/ui/badge'
import type { BatchStatus, RowStatus } from './types'

export const ACTIVE_STATUSES: BatchStatus[] = ['pending', 'processing', 'activating']
export const RESUMABLE_STATUSES: BatchStatus[] = ['creation_failed', 'reconciliation_failed', 'activation_failed']

export const batchStatusInfo: Record<BatchStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: 'Pending', variant: 'neutral' },
  processing: { label: 'Processing', variant: 'info' },
  activating: { label: 'Activating', variant: 'info' },
  completed: { label: 'Completed', variant: 'success' },
  creation_failed: { label: 'Creation failed', variant: 'danger' },
  reconciliation_failed: { label: 'Verification failed', variant: 'danger' },
  activation_failed: { label: 'Activation failed', variant: 'warning' },
}

export const rowStatusInfo: Record<RowStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: 'Pending', variant: 'neutral' },
  created: { label: 'Created, inactive', variant: 'info' },
  created_and_activated: { label: 'Active', variant: 'success' },
  failed: { label: 'Failed', variant: 'danger' },
}

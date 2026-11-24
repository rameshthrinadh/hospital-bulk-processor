// Mirrors app/schemas.py and app/store.py in the backend.

export type BatchStatus =
  | 'pending'
  | 'processing'
  | 'activating'
  | 'completed'
  | 'creation_failed'
  | 'reconciliation_failed'
  | 'activation_failed'

export type RowStatus = 'pending' | 'created' | 'created_and_activated' | 'failed'

export type HospitalResult = {
  row: number
  hospital_id: number | null
  name: string
  status: RowStatus
  error: string | null
}

export type BulkResult = {
  batch_id: string
  total_hospitals: number
  processed_hospitals: number
  failed_hospitals: number
  processing_time_seconds: number
  batch_activated: boolean
  hospitals: HospitalResult[]
  status: BatchStatus
  error: string | null
  created_at: string
  updated_at: string
}

export type RowError = {
  row: number | null // null for file-level problems
  message: string
}

export type ValidatedRow = {
  row: number
  name: string
  address: string
  phone: string | null
}

export type ValidationResult = {
  valid: boolean
  total_hospitals: number
  errors: RowError[]
  hospitals: ValidatedRow[]
}

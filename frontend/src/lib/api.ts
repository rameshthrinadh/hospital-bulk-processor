import { API_URL } from './config'
import type { BulkResult, RowError, ValidationResult } from './types'

export class ApiError extends Error {
  status: number // 0 when the server could not be reached
  errors: RowError[]

  constructor(status: number, message: string, errors: RowError[] = []) {
    super(message)
    this.status = status
    this.errors = errors
  }
}

function errorMessage(status: number, detail: unknown) {
  if (status === 413) return 'The file is too large for the server.'
  if (status === 404) return 'This batch is no longer available. Batches are kept in memory and are lost when the server restarts.'
  if (typeof detail === 'string') return detail
  if (status >= 500) return 'The server ran into a problem. Please try again.'
  return `Request failed (HTTP ${status}).`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(API_URL + path, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and that the backend is running.')
  }
  if (response.ok) return response.json()

  const body = await response.json().catch(() => null)
  throw new ApiError(response.status, errorMessage(response.status, body?.detail), body?.errors ?? [])
}

function csvForm(file: File) {
  const form = new FormData()
  form.append('file', file)
  return form
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  validate: (file: File) =>
    request<ValidationResult>('/hospitals/bulk/validate', { method: 'POST', body: csvForm(file) }),

  // wait=false: the server returns as soon as the batch has started.
  startBatch: (file: File) =>
    request<BulkResult>('/hospitals/bulk?wait=false', { method: 'POST', body: csvForm(file) }),

  getBatch: (id: string) => request<BulkResult>(`/hospitals/bulk/${id}`),

  resumeBatch: (id: string) =>
    request<BulkResult>(`/hospitals/bulk/${id}/resume?wait=false`, { method: 'POST' }),

  batchSocketUrl: (id: string) => `${API_URL.replace(/^http/, 'ws')}/hospitals/bulk/${id}/ws`,
}

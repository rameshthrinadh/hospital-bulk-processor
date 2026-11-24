export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '')
export const MAX_ROWS = Number(import.meta.env.VITE_MAX_ROWS || 20)
export const MAX_UPLOAD_BYTES = Number(import.meta.env.VITE_MAX_UPLOAD_BYTES || 64 * 1024)

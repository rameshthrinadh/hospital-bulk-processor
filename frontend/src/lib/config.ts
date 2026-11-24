// Production builds are served by the API itself, so by default they call
// their own origin. The Vite dev server runs separately, on another port.
const defaultApiUrl = import.meta.env.DEV ? 'http://localhost:8000' : window.location.origin

export const API_URL = (import.meta.env.VITE_API_URL || defaultApiUrl).replace(/\/$/, '')
export const MAX_ROWS = Number(import.meta.env.VITE_MAX_ROWS || 20)
export const MAX_UPLOAD_BYTES = Number(import.meta.env.VITE_MAX_UPLOAD_BYTES || 64 * 1024)

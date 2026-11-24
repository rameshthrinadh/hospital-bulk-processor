import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { BulkResult } from '@/lib/types'

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'closed'

const RETRY_MS = 3000

// Follows a batch over the backend WebSocket. The server sends the current
// state on connect, pushes every change, and closes normally (1000) once the
// run is over, so a finished batch costs one message and no polling.
export function useBatch(id: string) {
  const [batch, setBatch] = useState<BulkResult | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [notFound, setNotFound] = useState(false)
  const [generation, setGeneration] = useState(0) // bump to reconnect, e.g. after resume

  useEffect(() => {
    let socket: WebSocket
    let retry: ReturnType<typeof setTimeout>
    let stopped = false

    function connect() {
      socket = new WebSocket(api.batchSocketUrl(id))
      socket.onopen = () => setConnection('live')
      socket.onmessage = (event) => setBatch(JSON.parse(event.data))
      socket.onclose = (event) => {
        if (stopped) return
        if (event.code === 1000) return setConnection('closed')
        if (event.code === 4404) {
          setNotFound(true)
          return setConnection('closed')
        }
        // Network drop or server restarting: try again shortly.
        setConnection('reconnecting')
        retry = setTimeout(connect, RETRY_MS)
      }
    }

    setConnection('connecting')
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      socket.close()
    }
  }, [id, generation])

  return { batch, setBatch, connection, notFound, reconnect: () => setGeneration((g) => g + 1) }
}

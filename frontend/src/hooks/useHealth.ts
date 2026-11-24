import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'

export type Health = 'checking' | 'online' | 'offline'

export function useHealth() {
  const [health, setHealth] = useState<Health>('checking')

  const check = useCallback(() => {
    setHealth('checking')
    api.health().then(
      () => setHealth('online'),
      () => setHealth('offline'),
    )
  }, [])

  useEffect(check, [check])

  return { health, check }
}

import { AlertCircle, CheckCircle2, Info } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

const styles = {
  error: { box: 'border-red-200 bg-red-50 text-red-800', icon: AlertCircle },
  success: { box: 'border-emerald-200 bg-emerald-50 text-emerald-800', icon: CheckCircle2 },
  info: { box: 'border-zinc-200 bg-zinc-50 text-zinc-700', icon: Info },
}

export function Alert({ tone = 'info', title, children, action }: {
  tone?: keyof typeof styles
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  const { box, icon: Icon } = styles[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cn('flex gap-3 rounded-lg border p-4 text-sm', box)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">{title}</p>
        {children && <div className="mt-1 opacity-90">{children}</div>}
      </div>
      {action}
    </div>
  )
}

import { cn } from '@/lib/utils'

export function Progress({ value, className }: { value: number; className?: string }) {
  const percent = Math.min(100, Math.max(0, value))
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
      className={cn('h-2 w-full overflow-hidden rounded-full bg-zinc-100', className)}
    >
      <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${percent}%` }} />
    </div>
  )
}

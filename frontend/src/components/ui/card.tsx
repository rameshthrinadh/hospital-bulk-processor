import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type DivProps = HTMLAttributes<HTMLDivElement>

export function Card({ className, ...props }: DivProps) {
  return <div className={cn('rounded-xl border border-zinc-200 bg-white shadow-xs', className)} {...props} />
}

export function CardHeader({ className, ...props }: DivProps) {
  return <div className={cn('flex flex-col gap-1 p-5 pb-0', className)} {...props} />
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn('text-sm font-semibold', className)} {...props} />
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-sm text-zinc-500', className)} {...props} />
}

export function CardContent({ className, ...props }: DivProps) {
  return <div className={cn('p-5', className)} {...props} />
}

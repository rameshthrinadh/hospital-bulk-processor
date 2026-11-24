import { useRef, type ReactNode } from 'react'
import { Button } from './button'

type ConfirmDialogProps = {
  title: string
  description: string
  confirmLabel: string
  onConfirm: () => void
  trigger: (open: () => void) => ReactNode
}

// Native <dialog>: focus trapping, Esc to close and the backdrop come for free.
export function ConfirmDialog({ title, description, confirmLabel, onConfirm, trigger }: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const close = () => ref.current?.close()

  return (
    <>
      {trigger(() => ref.current?.showModal())}
      <dialog
        ref={ref}
        className="m-auto w-[calc(100%-2rem)] max-w-sm rounded-xl border border-zinc-200 p-6 shadow-lg backdrop:bg-zinc-900/30"
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-2 text-sm text-zinc-500">{description}</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={close}>Cancel</Button>
          <Button variant="destructive" onClick={() => { close(); onConfirm() }}>{confirmLabel}</Button>
        </div>
      </dialog>
    </>
  )
}

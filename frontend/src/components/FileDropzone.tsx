import { FileText, UploadCloud, X } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { Button } from '@/components/ui/button'
import { cn, formatBytes } from '@/lib/utils'

type FileDropzoneProps = {
  file: File | null
  onSelect: (file: File) => void
  onRemove: () => void
}

export function FileDropzone({ file, onSelect, onRemove }: FileDropzoneProps) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files[0]
    if (dropped) onSelect(dropped)
  }

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-zinc-200 bg-white p-4">
        <span className="flex size-10 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <FileText className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{file.name}</p>
          <p className="text-xs text-zinc-500">{formatBytes(file.size)}</p>
        </div>
        <Button variant="ghost" size="icon" onClick={onRemove} aria-label="Remove file">
          <X />
        </Button>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={cn(
        'flex w-full flex-col items-center rounded-xl border border-dashed border-zinc-300 bg-white px-6 py-12 text-center transition-colors',
        'hover:border-zinc-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40',
        dragging && 'border-accent bg-accent-soft',
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-full bg-zinc-100 text-zinc-500">
        <UploadCloud className="size-5" />
      </span>
      <p className="mt-4 text-sm font-medium">Drop your CSV here, or <span className="text-accent">browse</span></p>
      <p className="mt-1 text-xs text-zinc-500">CSV files only</p>
      <input
        ref={input}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e) => {
          const picked = e.target.files?.[0]
          if (picked) onSelect(picked)
          e.target.value = '' // allow re-selecting the same file after fixing it
        }}
      />
    </button>
  )
}

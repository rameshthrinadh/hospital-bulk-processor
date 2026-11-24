import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <Button variant="ghost" size="icon" className="size-6" onClick={copy} aria-label={copied ? 'Copied' : label}>
      {copied ? <Check className="text-emerald-600" /> : <Copy className="text-zinc-400" />}
    </Button>
  )
}

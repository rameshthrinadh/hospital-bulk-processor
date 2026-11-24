import { Download, Loader2, Play } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { Alert } from '@/components/Alert'
import { FileDropzone } from '@/components/FileDropzone'
import { PageHeader } from '@/components/PageHeader'
import { ValidationReport } from '@/components/ValidationReport'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { api, ApiError } from '@/lib/api'
import { MAX_ROWS, MAX_UPLOAD_BYTES } from '@/lib/config'
import { addToHistory } from '@/lib/history'
import type { ValidationResult } from '@/lib/types'
import { formatBytes } from '@/lib/utils'

const TEMPLATE = 'name,address,phone\nGeneral Hospital,123 Main St,555-1234\nCity Clinic,"45 Side St, Suite 2",\n'

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([TEMPLATE], { type: 'text/csv' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'hospitals-template.csv'
  link.click()
  URL.revokeObjectURL(url)
}

function FormatGuide() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>CSV format</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-600">
        <ul className="space-y-2">
          <li><code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">name</code> required</li>
          <li><code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">address</code> required</li>
          <li><code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">phone</code> optional</li>
        </ul>
        <p>
          First line must be the header. At most {MAX_ROWS} hospitals and {formatBytes(MAX_UPLOAD_BYTES)}.
          If any row is invalid, nothing is created.
        </p>
        <Button variant="outline" size="sm" onClick={downloadTemplate}>
          <Download /> Download template
        </Button>
      </CardContent>
    </Card>
  )
}

export function Upload() {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [validating, setValidating] = useState(false)
  const [starting, setStarting] = useState(false)

  function reset() {
    setFile(null)
    setValidation(null)
    setError(null)
  }

  async function selectFile(picked: File) {
    reset()
    if (!picked.name.toLowerCase().endsWith('.csv')) return setError('Please choose a .csv file.')
    if (picked.size > MAX_UPLOAD_BYTES) {
      return setError(`This file is ${formatBytes(picked.size)}. The limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`)
    }
    setFile(picked)
    setValidating(true)
    try {
      setValidation(await api.validate(picked))
    } catch (err) {
      setError((err as ApiError).message)
    } finally {
      setValidating(false)
    }
  }

  async function startProcessing() {
    if (!file) return
    setStarting(true)
    setError(null)
    try {
      const batch = await api.startBatch(file)
      addToHistory(batch.batch_id, file.name)
      navigate(`/batches/${batch.batch_id}`)
    } catch (err) {
      const e = err as ApiError
      // A 422 here carries the same row errors as validation.
      if (e.errors.length) setValidation({ valid: false, total_hospitals: 0, errors: e.errors, hospitals: [] })
      else setError(e.message)
      setStarting(false)
    }
  }

  return (
    <>
      <PageHeader title="Upload CSV" description="We check the file first. Nothing is created until you start processing." />

      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="min-w-0 space-y-6">
          <FileDropzone file={file} onSelect={selectFile} onRemove={reset} />

          {error && <Alert tone="error" title="Something went wrong">{error}</Alert>}

          {validating && (
            <p className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 className="size-4 animate-spin" /> Checking your file…
            </p>
          )}

          {validation && <ValidationReport result={validation} />}

          {file && validation?.valid && (
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button variant="outline" onClick={reset} disabled={starting}>Choose another file</Button>
              <Button onClick={startProcessing} disabled={starting}>
                {starting ? <Loader2 className="animate-spin" /> : <Play />}
                {starting ? 'Starting…' : `Process ${validation.total_hospitals} hospitals`}
              </Button>
            </div>
          )}
        </div>

        <FormatGuide />
      </div>
    </>
  )
}

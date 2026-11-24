import { Alert } from '@/components/Alert'
import { Card } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { ValidationResult } from '@/lib/types'

export function ValidationReport({ result }: { result: ValidationResult }) {
  if (result.valid) {
    return (
      <div className="space-y-4">
        <Alert tone="success" title={`All ${result.total_hospitals} rows are valid`}>
          Review the hospitals below, then start processing.
        </Alert>
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-14">Row</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Address</TableHead>
                <TableHead>Phone</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.hospitals.map((h) => (
                <TableRow key={h.row}>
                  <TableCell className="tabular-nums text-zinc-500">{h.row}</TableCell>
                  <TableCell className="font-medium">{h.name}</TableCell>
                  <TableCell className="text-zinc-600">{h.address}</TableCell>
                  <TableCell className="text-zinc-600">{h.phone ?? <span className="text-zinc-400">—</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </div>
    )
  }

  const fileErrors = result.errors.filter((e) => e.row === null)
  const rowErrors = result.errors.filter((e) => e.row !== null)
  const invalidRows = new Set(rowErrors.map((e) => e.row)).size

  return (
    <div className="space-y-4">
      <Alert tone="error" title="This CSV can't be processed yet">
        {invalidRows > 0 && `${invalidRows} row${invalidRows === 1 ? ' has' : 's have'} problems. `}
        Nothing has been created. Fix the issues below and upload the file again.
      </Alert>
      <Card className="divide-y divide-zinc-100">
        {[...fileErrors, ...rowErrors].map((error, i) => (
          <div key={i} className="flex gap-4 px-5 py-3 text-sm">
            <span className="w-16 shrink-0 text-zinc-500">{error.row === null ? 'File' : `Row ${error.row}`}</span>
            <span className="text-red-700">{error.message}</span>
          </div>
        ))}
      </Card>
    </div>
  )
}

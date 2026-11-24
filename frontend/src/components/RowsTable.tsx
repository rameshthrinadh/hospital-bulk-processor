import { RowStatusBadge } from '@/components/StatusBadge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { HospitalResult } from '@/lib/types'

const dash = <span className="text-zinc-400">—</span>

// A table on wider screens, stacked rows on phones.
export function RowsTable({ rows }: { rows: HospitalResult[] }) {
  return (
    <>
      <div className="hidden sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-14">Row</TableHead>
              <TableHead>Hospital</TableHead>
              <TableHead>Hospital ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Error</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.row}>
                <TableCell className="tabular-nums text-zinc-500">{r.row}</TableCell>
                <TableCell className="font-medium">{r.name}</TableCell>
                <TableCell className="font-mono text-xs">{r.hospital_id ?? dash}</TableCell>
                <TableCell><RowStatusBadge status={r.status} /></TableCell>
                <TableCell className="max-w-xs text-red-700">{r.error ?? dash}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <ul className="divide-y divide-zinc-100 sm:hidden">
        {rows.map((r) => (
          <li key={r.row} className="space-y-1.5 px-4 py-3 text-sm">
            <div className="flex items-start justify-between gap-3">
              <span className="font-medium">{r.row}. {r.name}</span>
              <RowStatusBadge status={r.status} />
            </div>
            <p className="text-xs text-zinc-500">ID: <span className="font-mono">{r.hospital_id ?? '—'}</span></p>
            {r.error && <p className="text-xs text-red-700">{r.error}</p>}
          </li>
        ))}
      </ul>
    </>
  )
}

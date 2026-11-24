import { FileQuestion } from 'lucide-react'
import { Link, Route, Routes } from 'react-router'
import { EmptyState } from '@/components/EmptyState'
import { Layout } from '@/components/Layout'
import { buttonVariants } from '@/components/ui/button'
import { BatchDetail } from '@/pages/BatchDetail'
import { Dashboard } from '@/pages/Dashboard'
import { History } from '@/pages/History'
import { Upload } from '@/pages/Upload'

function NotFound() {
  return (
    <EmptyState
      icon={FileQuestion}
      title="Page not found"
      description="The page you're looking for doesn't exist."
      action={<Link to="/" className={buttonVariants({ variant: 'outline' })}>Go to dashboard</Link>}
    />
  )
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="upload" element={<Upload />} />
        <Route path="history" element={<History />} />
        <Route path="batches/:id" element={<BatchDetail />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

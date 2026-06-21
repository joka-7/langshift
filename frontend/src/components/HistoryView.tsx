import { useEffect, useState } from 'react'
import { getJobs } from '../api'
import type { JobSummary } from '../types'

interface Props {
  onSelect: (jobId: string) => void
  refreshKey: number
}

export function HistoryView({ onSelect, refreshKey }: Props) {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getJobs().then(setJobs).catch((e) => setError(e.message))
  }, [refreshKey])

  if (error) return <p className="error">{error}</p>
  if (jobs.length === 0) return <p>No translation runs yet.</p>

  return (
    <table className="history-table">
      <thead>
        <tr>
          <th>When</th>
          <th>Input</th>
          <th>From → To</th>
          <th>Provider</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id}>
            <td>{new Date(job.created_at * 1000).toLocaleString()}</td>
            <td title={job.input_path}>{job.input_path}</td>
            <td>{job.from_lang} → {job.to_lang}</td>
            <td>{job.provider}/{job.model}</td>
            <td className={`status status-${job.status}`}>{job.status}</td>
            <td>
              <button onClick={() => onSelect(job.id)}>View</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

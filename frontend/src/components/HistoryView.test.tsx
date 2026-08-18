import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HistoryView } from './HistoryView'
import * as api from '../api'
import type { JobSummary } from '../types'

vi.mock('../api')

const JOB: JobSummary = {
  id: 'job-1',
  input_path: '/repo',
  output_path: '/repo_python',
  from_lang: 'typescript',
  to_lang: 'python',
  provider: 'offline',
  model: 'n/a',
  status: 'done',
  error: null,
  created_at: 1735689600, // 2025-01-01T00:00:00Z
}

describe('HistoryView', () => {
  beforeEach(() => {
    vi.mocked(api.getJobs).mockReset()
  })

  it('shows a placeholder when there are no jobs', async () => {
    vi.mocked(api.getJobs).mockResolvedValueOnce([])
    render(<HistoryView onSelect={vi.fn()} refreshKey={0} />)
    expect(await screen.findByText('No translation runs yet.')).toBeInTheDocument()
  })

  it('renders a row per job with its languages, provider, and status', async () => {
    vi.mocked(api.getJobs).mockResolvedValueOnce([JOB])
    render(<HistoryView onSelect={vi.fn()} refreshKey={0} />)

    await screen.findByText('/repo')
    expect(screen.getByText('typescript → python')).toBeInTheDocument()
    expect(screen.getByText('offline/n/a')).toBeInTheDocument()
    expect(screen.getByText('done')).toBeInTheDocument()
  })

  it('calls onSelect with the job id when View is clicked', async () => {
    vi.mocked(api.getJobs).mockResolvedValueOnce([JOB])
    const onSelect = vi.fn()
    render(<HistoryView onSelect={onSelect} refreshKey={0} />)

    const button = await screen.findByRole('button', { name: 'View' })
    await userEvent.click(button)
    expect(onSelect).toHaveBeenCalledWith('job-1')
  })

  it('shows the error message when the fetch fails', async () => {
    vi.mocked(api.getJobs).mockRejectedValueOnce(new Error('server unreachable'))
    render(<HistoryView onSelect={vi.fn()} refreshKey={0} />)
    expect(await screen.findByText('server unreachable')).toBeInTheDocument()
  })

  it('re-fetches when refreshKey changes', async () => {
    vi.mocked(api.getJobs).mockResolvedValue([])
    const { rerender } = render(<HistoryView onSelect={vi.fn()} refreshKey={0} />)
    await waitFor(() => expect(api.getJobs).toHaveBeenCalledTimes(1))

    rerender(<HistoryView onSelect={vi.fn()} refreshKey={1} />)
    await waitFor(() => expect(api.getJobs).toHaveBeenCalledTimes(2))
  })
})

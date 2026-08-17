import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OutputBrowser } from './OutputBrowser'
import * as api from '../api'
import type { TreeNode } from '../types'

vi.mock('../api')

const TREE: TreeNode = {
  name: '',
  type: 'dir',
  children: [
    { name: 'main.py', type: 'file' },
    {
      name: 'utils',
      type: 'dir',
      children: [{ name: 'helpers.py', type: 'file' }],
    },
  ],
}

describe('OutputBrowser', () => {
  beforeEach(() => {
    vi.mocked(api.getTree).mockReset()
    vi.mocked(api.getFile).mockReset()
  })

  it('shows a loading message before the tree arrives', () => {
    vi.mocked(api.getTree).mockReturnValue(new Promise(() => {})) // never resolves
    render(<OutputBrowser jobId="job-1" />)
    expect(screen.getByText('Loading output tree...')).toBeInTheDocument()
  })

  it('renders the file tree, including nested directories', async () => {
    vi.mocked(api.getTree).mockResolvedValueOnce(TREE)
    render(<OutputBrowser jobId="job-1" />)

    expect(await screen.findByText('main.py')).toBeInTheDocument()
    expect(screen.getByText('utils')).toBeInTheDocument()
    expect(screen.getByText('helpers.py')).toBeInTheDocument()
  })

  it('fetches and displays a file\'s source and translated content on selection', async () => {
    vi.mocked(api.getTree).mockResolvedValueOnce(TREE)
    vi.mocked(api.getFile).mockResolvedValueOnce({
      path: 'main.py',
      translated: 'def add(a, b):\n    return a + b',
      source: 'function add(a, b) { return a + b; }',
    })

    render(<OutputBrowser jobId="job-1" />)
    await userEvent.click(await screen.findByText('main.py'))

    expect(await screen.findByText(/def add/)).toBeInTheDocument()
    expect(screen.getByText(/function add/)).toBeInTheDocument()
    expect(api.getFile).toHaveBeenCalledWith('job-1', 'main.py')
  })

  it('shows a placeholder for source when no matching source file exists', async () => {
    vi.mocked(api.getTree).mockResolvedValueOnce(TREE)
    vi.mocked(api.getFile).mockResolvedValueOnce({
      path: 'main.py',
      translated: 'x = 1',
      source: null,
    })

    render(<OutputBrowser jobId="job-1" />)
    await userEvent.click(await screen.findByText('main.py'))

    expect(await screen.findByText('(no matching source file found)')).toBeInTheDocument()
  })

  it('shows an error message when the tree fetch fails', async () => {
    vi.mocked(api.getTree).mockRejectedValueOnce(new Error('job not found'))
    render(<OutputBrowser jobId="job-1" />)
    expect(await screen.findByText('job not found')).toBeInTheDocument()
  })

  it('builds nested paths correctly for files inside subdirectories', async () => {
    vi.mocked(api.getTree).mockResolvedValueOnce(TREE)
    vi.mocked(api.getFile).mockResolvedValueOnce({
      path: 'utils/helpers.py', translated: 'x', source: null,
    })

    render(<OutputBrowser jobId="job-1" />)
    await userEvent.click(await screen.findByText('helpers.py'))

    expect(api.getFile).toHaveBeenCalledWith('job-1', 'utils/helpers.py')
  })
})

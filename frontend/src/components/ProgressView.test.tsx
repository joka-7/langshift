import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProgressView } from './ProgressView'

// jsdom does not implement EventSource, so ProgressView's SSE connection is
// driven through this fake: tests emit messages/close/error on the most
// recently constructed instance the way a real server-sent-events stream
// would arrive.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  private closeListeners: Array<() => void> = []

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: () => void) {
    if (type === 'close') this.closeListeners.push(cb)
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  emitClose() {
    this.closeListeners.forEach((cb) => cb())
  }

  emitError() {
    this.onerror?.()
  }

  close() {
    this.closed = true
  }
}

describe('ProgressView', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('connects to the job-specific SSE endpoint', () => {
    render(<ProgressView jobId="job-42" onFinished={vi.fn()} />)
    expect(FakeEventSource.instances[0].url).toBe('/api/jobs/job-42/stream')
  })

  it('renders each incoming event in the log', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({ type: 'manifest_done', count: 2 })
    })

    expect(screen.getByText('Translated 2 manifest file(s)')).toBeInTheDocument()
  })

  it('shows a progress bar reflecting the latest file_start event', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({ type: 'file_start', index: 2, total: 4, path: 'b.ts' })
    })

    expect(screen.getByText('[2/4] Translating b.ts...')).toBeInTheDocument()
  })

  it('describes a file_done event including confidence when present', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({
        type: 'file_done', index: 1, total: 1, path: 'a.ts',
        status: 'ok', attempts: 1, confidence: 92,
      })
    })

    expect(screen.getByText(/a\.ts → ok \(attempts: 1, confidence: 92\)/)).toBeInTheDocument()
  })

  it('describes a tests_retry event', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({ type: 'tests_retry', attempt: 2, total: 3 })
    })

    expect(screen.getByText('Tests failed, retrying (attempt 2/3)...')).toBeInTheDocument()
  })

  it('describes the finished event summary', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({
        type: 'finished',
        summary: { translated: 3, total: 4, failed: 1, needs_review: 0 },
      })
    })

    expect(screen.getByText('Finished: 3/4 translated, 1 failed, 0 need review')).toBeInTheDocument()
  })

  it('flags error events with the error class', () => {
    render(<ProgressView jobId="job-1" onFinished={vi.fn()} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitMessage({ type: 'error', message: 'boom' })
    })

    const item = screen.getByText('Error: boom')
    expect(item).toHaveClass('error')
  })

  it('calls onFinished and closes the connection on a close event', () => {
    const onFinished = vi.fn()
    render(<ProgressView jobId="job-1" onFinished={onFinished} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitClose()
    })

    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(source.closed).toBe(true)
  })

  it('calls onFinished exactly once even if error fires after close', () => {
    const onFinished = vi.fn()
    render(<ProgressView jobId="job-1" onFinished={onFinished} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitClose()
      source.emitError()
    })

    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('calls onFinished on a connection error too', () => {
    const onFinished = vi.fn()
    render(<ProgressView jobId="job-1" onFinished={onFinished} />)
    const source = FakeEventSource.instances[0]

    act(() => {
      source.emitError()
    })

    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(source.closed).toBe(true)
  })
})

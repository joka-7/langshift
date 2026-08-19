import { useEffect, useRef, useState } from 'react'
import type { ProgressEvent } from '../types'

interface Props {
  jobId: string
  onFinished: () => void
}

function describe(event: ProgressEvent): string {
  switch (event.type) {
    case 'manifest_done':
      return `Translated ${event.count} manifest file(s)`
    case 'file_start':
      return `[${event.index}/${event.total}] Translating ${event.path}...`
    case 'file_done':
      return `[${event.index}/${event.total}] ${event.path} → ${event.status} (attempts: ${event.attempts}${
        event.confidence != null ? `, confidence: ${event.confidence}` : ''
      })`
    case 'tests_retry':
      return `Tests failed, retrying (attempt ${event.attempt}/${event.total})...`
    case 'tests_done':
      return `Tests: ${event.passed ? 'passed' : 'failed'}`
    case 'finished': {
      const s = event.summary as Record<string, number>
      return `Finished: ${s.translated}/${s.total} translated, ${s.failed} failed, ${s.needs_review} need review`
    }
    case 'error':
      return `Error: ${event.message}`
    default:
      return JSON.stringify(event)
  }
}

export function ProgressView({ jobId, onFinished }: Props) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const finishedRef = useRef(false)
  const logRef = useRef<HTMLUListElement | null>(null)

  useEffect(() => {
    finishedRef.current = false
    const source = new EventSource(`/api/jobs/${jobId}/stream`)

    source.onmessage = (e) => {
      const event = JSON.parse(e.data) as ProgressEvent
      setEvents((prev) => [...prev, event])
    }
    source.addEventListener('close', () => {
      source.close()
      if (!finishedRef.current) {
        finishedRef.current = true
        onFinished()
      }
    })
    source.onerror = () => {
      source.close()
      if (!finishedRef.current) {
        finishedRef.current = true
        onFinished()
      }
    }

    return () => source.close()
  }, [jobId, onFinished])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [events])

  const lastFileEvent = [...events].reverse().find((e) => e.type === 'file_done' || e.type === 'file_start')
  const total = (lastFileEvent?.total as number | undefined) ?? 0
  const index = (lastFileEvent?.index as number | undefined) ?? 0

  return (
    <div className="progress-view">
      <h3>Translating...</h3>
      {total > 0 && (
        <div className="progress-bar">
          <div className="progress-bar-fill" style={{ width: `${(index / total) * 100}%` }} />
        </div>
      )}
      <ul className="event-log" ref={logRef}>
        {events.map((event, i) => (
          <li key={i} className={event.type === 'error' ? 'error' : undefined}>
            {describe(event)}
          </li>
        ))}
      </ul>
    </div>
  )
}

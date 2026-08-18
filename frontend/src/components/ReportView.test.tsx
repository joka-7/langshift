import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ReportView } from './ReportView'
import type { TranslationReport } from '../types'

function makeReport(overrides: Partial<TranslationReport> = {}): TranslationReport {
  return {
    from_lang: 'typescript',
    to_lang: 'python',
    input_path: '/in',
    output_path: '/out',
    started_at: '2026-01-01T00:00:00',
    elapsed_seconds: 12.345,
    summary: {
      total: 3,
      translated: 2,
      failed: 1,
      skipped: 0,
      needed_retry: 1,
      manifests_translated: 1,
      high_confidence: 1,
      needs_review: 1,
    },
    files: [
      { path: 'a.ts', status: 'ok', attempts: 1, error: null },
      { path: 'b.ts', status: 'ok_with_warnings', attempts: 2, error: null },
      { path: 'c.ts', status: 'failed', attempts: 3, error: 'SyntaxError: bad' },
    ],
    manifest_translated: ['/out/requirements.txt'],
    tests_passed: null,
    test_output: null,
    ...overrides,
  }
}

describe('ReportView', () => {
  it('renders the summary stat grid', () => {
    render(<ReportView report={makeReport()} />)
    expect(screen.getByText('typescript', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Total')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument() // total
    expect(screen.getByText('2')).toBeInTheDocument() // translated
  })

  it('shows elapsed seconds rounded to 2 decimals', () => {
    render(<ReportView report={makeReport()} />)
    expect(screen.getByText(/12\.35s/)).toBeInTheDocument()
  })

  it('lists only non-ok files under Issues', () => {
    render(<ReportView report={makeReport()} />)
    expect(screen.getByText('Issues')).toBeInTheDocument()
    expect(screen.getByText('b.ts', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('c.ts', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('a.ts', { exact: false })).not.toBeInTheDocument()
  })

  it('shows the failed file error message', () => {
    render(<ReportView report={makeReport()} />)
    expect(screen.getByText(/SyntaxError: bad/)).toBeInTheDocument()
  })

  it('omits the Issues section when every file is ok', () => {
    const report = makeReport({
      files: [{ path: 'a.ts', status: 'ok', attempts: 1, error: null }],
    })
    render(<ReportView report={report} />)
    expect(screen.queryByText('Issues')).not.toBeInTheDocument()
  })

  it('shows a Tests stat when tests_passed is set — regression for the field the backend used to drop', () => {
    // Regression: report.save() used to omit tests_passed/test_output from
    // translation_report.json entirely, and even after that was fixed the
    // frontend read report.summary.tests_passed while the backend writes
    // the top-level report.tests_passed — so this stat never rendered
    // either way. Both are now aligned on the top-level field.
    render(<ReportView report={makeReport({ tests_passed: true })} />)
    expect(screen.getByText('Tests')).toBeInTheDocument()
    expect(screen.getByText('passed')).toBeInTheDocument()
  })

  it('shows "failed" for the Tests stat when tests_passed is false', () => {
    render(<ReportView report={makeReport({ tests_passed: false })} />)
    const testsLabel = screen.getByText('Tests')
    expect(testsLabel.previousElementSibling).toHaveTextContent('failed')
  })

  it('omits the Tests stat entirely when tests_passed is null', () => {
    render(<ReportView report={makeReport({ tests_passed: null })} />)
    expect(screen.queryByText('Tests')).not.toBeInTheDocument()
  })
})

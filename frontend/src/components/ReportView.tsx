import type { TranslationReport } from '../types'

export function ReportView({ report }: { report: TranslationReport }) {
  const s = report.summary
  return (
    <div className="report-view">
      <h3>Report</h3>
      <p>
        {report.from_lang} → {report.to_lang} · {report.elapsed_seconds.toFixed(2)}s ·
        {' '}{report.output_path}
      </p>
      <div className="stat-grid">
        <Stat label="Total" value={s.total} />
        <Stat label="Translated" value={s.translated} />
        <Stat label="Failed" value={s.failed} />
        <Stat label="Skipped" value={s.skipped} />
        <Stat label="Needed retry" value={s.needed_retry} />
        <Stat label="High confidence" value={s.high_confidence} />
        <Stat label="Needs review" value={s.needs_review} />
        {s.tests_passed != null && (
          <Stat label="Tests" value={s.tests_passed ? 'passed' : 'failed'} />
        )}
      </div>
      {report.files.some((f) => f.status !== 'ok') && (
        <div className="file-issues">
          <h4>Issues</h4>
          <ul>
            {report.files
              .filter((f) => f.status !== 'ok')
              .map((f) => (
                <li key={f.path}>
                  <strong>{f.path}</strong>: {f.status} {f.error ? `— ${f.error}` : ''}
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

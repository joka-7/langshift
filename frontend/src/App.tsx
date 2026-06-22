import { useCallback, useState } from 'react'
import { postEstimate, postJob, getReport } from './api'
import { TranslateForm } from './components/TranslateForm'
import { EstimatePanel } from './components/EstimatePanel'
import { ProgressView } from './components/ProgressView'
import { ReportView } from './components/ReportView'
import { OutputBrowser } from './components/OutputBrowser'
import { HistoryView } from './components/HistoryView'
import type { EstimateResult, TranslateFormValues, TranslationReport } from './types'
import './App.css'

type Tab = 'translate' | 'history'
type Stage = 'form' | 'estimate' | 'progress' | 'report'

function App() {
  const [tab, setTab] = useState<Tab>('translate')
  const [stage, setStage] = useState<Stage>('form')
  const [pendingValues, setPendingValues] = useState<TranslateFormValues | null>(null)
  const [estimate, setEstimate] = useState<EstimateResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [report, setReport] = useState<TranslationReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0)

  async function handleEstimate(values: TranslateFormValues) {
    setError(null)
    setBusy(true)
    try {
      const result = await postEstimate(values)
      setEstimate(result)
      setPendingValues(values)
      setStage('estimate')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function startJob(values: TranslateFormValues) {
    setError(null)
    setBusy(true)
    try {
      const job = await postJob(values)
      setJobId(job.id)
      setStage('progress')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const handleFinished = useCallback(() => {
    if (!jobId) return
    getReport(jobId)
      .then((r) => {
        setReport(r)
        setStage('report')
        setHistoryRefreshKey((k) => k + 1)
      })
      .catch((e) => setError(e.message))
  }, [jobId])

  function reset() {
    setStage('form')
    setEstimate(null)
    setPendingValues(null)
    setJobId(null)
    setReport(null)
    setError(null)
  }

  function viewHistoryJob(id: string) {
    setJobId(id)
    setTab('translate')
    setBusy(true)
    getReport(id)
      .then((r) => {
        setReport(r)
        setStage('report')
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <div className="app">
      <header>
        <h1>repo-translator</h1>
        <nav>
          <button className={tab === 'translate' ? 'active' : ''} onClick={() => setTab('translate')}>
            Translate
          </button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
            History
          </button>
        </nav>
      </header>

      {error && <p className="error">{error}</p>}

      {tab === 'history' && <HistoryView onSelect={viewHistoryJob} refreshKey={historyRefreshKey} />}

      {tab === 'translate' && (
        <>
          {stage === 'form' && (
            <TranslateForm onEstimate={handleEstimate} onTranslate={startJob} busy={busy} />
          )}

          {stage === 'estimate' && estimate && pendingValues && (
            <EstimatePanel
              estimate={estimate}
              busy={busy}
              onCancel={() => setStage('form')}
              onConfirm={() => startJob(pendingValues)}
            />
          )}

          {stage === 'progress' && jobId && (
            <ProgressView key={jobId} jobId={jobId} onFinished={handleFinished} />
          )}

          {stage === 'report' && report && jobId && (
            <>
              <ReportView report={report} />
              <OutputBrowser jobId={jobId} />
              <div className="row actions">
                <button onClick={reset}>Start another translation</button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

export default App

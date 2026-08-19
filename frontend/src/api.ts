import type {
  EstimateResult,
  FileContent,
  JobSummary,
  Language,
  ProvidersResponse,
  TranslateFormValues,
  TranslationReport,
  TreeNode,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail ?? `Request failed: ${res.status}`)
  }
  return res.json()
}

export function getLanguages(): Promise<Language[]> {
  return request('/api/languages')
}

export function getProviders(): Promise<ProvidersResponse> {
  return request('/api/providers')
}

function estimateOrJobPayload(values: TranslateFormValues) {
  return {
    input_path: values.input_path,
    from_lang: values.from_lang,
    to_lang: values.to_lang,
    provider: values.provider,
    model: values.model,
    translate_manifests: values.translate_manifests,
    score_confidence: values.score_confidence,
    base_url: values.base_url || undefined,
  }
}

export function postEstimate(values: TranslateFormValues): Promise<EstimateResult> {
  return request('/api/estimate', {
    method: 'POST',
    body: JSON.stringify(estimateOrJobPayload(values)),
  })
}

export function postJob(values: TranslateFormValues): Promise<JobSummary> {
  return request('/api/jobs', {
    method: 'POST',
    body: JSON.stringify({
      ...estimateOrJobPayload(values),
      output_path: values.output_path || undefined,
      api_key: values.api_key || undefined,
      run_tests: values.run_tests,
      resume: values.resume,
      cross_file_context: values.cross_file_context,
    }),
  })
}

export function getJobs(): Promise<JobSummary[]> {
  return request('/api/jobs')
}

export function getJob(jobId: string): Promise<JobSummary> {
  return request(`/api/jobs/${jobId}`)
}

export function getReport(jobId: string): Promise<TranslationReport> {
  return request(`/api/jobs/${jobId}/report`)
}

export function getTree(jobId: string): Promise<TreeNode> {
  return request(`/api/jobs/${jobId}/tree`)
}

export function getFile(jobId: string, path: string): Promise<FileContent> {
  return request(`/api/jobs/${jobId}/file?path=${encodeURIComponent(path)}`)
}

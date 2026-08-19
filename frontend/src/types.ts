export interface Language {
  name: string
  aliases: string[]
  has_runner: boolean
  has_test_runner: boolean
}

export interface Provider {
  name: string
  models: string[]
  freeform_model: boolean
}

export interface ProvidersResponse {
  providers: Provider[]
  offline_pairs: string[]
}

export interface EstimateResult {
  from_lang: string
  to_lang: string
  provider: string
  model: string
  file_count: number
  manifest_count: number
  input_tokens: number
  output_tokens: number
  // null for openai-compat and any provider/model pair with no known pricing
  // (agent.estimate_translation returns None in those cases).
  estimated_cost: number | null
  price_label: string
}

export interface JobSummary {
  id: string
  input_path: string
  output_path: string
  from_lang: string
  to_lang: string
  provider: string
  model: string
  status: 'running' | 'done' | 'failed'
  error: string | null
  created_at: number
}

export interface ProgressEvent {
  type: 'manifest_done' | 'file_start' | 'file_done' | 'tests_retry' | 'tests_done'
    | 'finished' | 'error'
  [key: string]: unknown
}

export interface FileResult {
  path: string
  status: string
  attempts: number
  error: string | null
  run_output?: string | null
  confidence?: number | null
  confidence_reason?: string | null
  chunks?: number | null
}

export interface ReportSummary {
  total: number
  translated: number
  failed: number
  skipped: number
  needed_retry: number
  manifests_translated: number
  high_confidence: number
  needs_review: number
}

export interface TranslationReport {
  from_lang: string
  to_lang: string
  input_path: string
  output_path: string
  started_at: string
  elapsed_seconds: number
  summary: ReportSummary
  files: FileResult[]
  manifest_translated: string[]
  // Populated only when --run-tests / run_tests was requested; null otherwise.
  tests_passed: boolean | null
  test_output: string | null
}

export interface TreeNode {
  name: string
  type: 'file' | 'dir'
  children?: TreeNode[]
}

export interface FileContent {
  path: string
  translated: string
  source: string | null
}

export interface TranslateFormValues {
  input_path: string
  output_path: string
  from_lang: string
  to_lang: string
  provider: string
  model: string
  api_key: string
  base_url: string
  run_tests: boolean
  translate_manifests: boolean
  score_confidence: boolean
  resume: boolean
}

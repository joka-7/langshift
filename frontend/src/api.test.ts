import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getFile,
  getJob,
  getJobs,
  getLanguages,
  getProviders,
  getReport,
  getTree,
  postEstimate,
  postJob,
} from './api'
import type { TranslateFormValues } from './types'

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

const BASE_VALUES: TranslateFormValues = {
  input_path: '/repo',
  output_path: '',
  from_lang: 'typescript',
  to_lang: 'python',
  provider: 'offline',
  model: 'n/a',
  api_key: '',
  base_url: '',
  run_tests: false,
  translate_manifests: true,
  score_confidence: true,
  resume: false,
}

describe('api', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('getLanguages GETs /api/languages', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse([{ name: 'typescript' }]))

    const result = await getLanguages()

    expect(mockFetch).toHaveBeenCalledWith('/api/languages', { headers: undefined })
    expect(result).toEqual([{ name: 'typescript' }])
  })

  it('getProviders GETs /api/providers', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ providers: [], offline_pairs: [] }))

    await getProviders()

    expect(mockFetch).toHaveBeenCalledWith('/api/providers', { headers: undefined })
  })

  it('postEstimate POSTs to /api/estimate with JSON body and content-type header', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ estimated_cost: 0.01 }))

    await postEstimate(BASE_VALUES)

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [path, init] = mockFetch.mock.calls[0]
    expect(path).toBe('/api/estimate')
    expect(init?.method).toBe('POST')
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' })
    const body = JSON.parse(init?.body as string)
    expect(body).toEqual({
      input_path: '/repo',
      from_lang: 'typescript',
      to_lang: 'python',
      provider: 'offline',
      model: 'n/a',
      translate_manifests: true,
      score_confidence: true,
      base_url: undefined,
    })
  })

  it('postJob POSTs to /api/jobs including output_path/api_key/run_tests/resume', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: 'job1' }))

    await postJob({
      ...BASE_VALUES, output_path: '/out', api_key: 'sk-1', run_tests: true, resume: true,
    })

    const [path, init] = mockFetch.mock.calls[0]
    expect(path).toBe('/api/jobs')
    const body = JSON.parse(init?.body as string)
    expect(body.output_path).toBe('/out')
    expect(body.api_key).toBe('sk-1')
    expect(body.run_tests).toBe(true)
    expect(body.resume).toBe(true)
  })

  it('postJob omits empty optional fields rather than sending empty strings', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ id: 'job1' }))

    await postJob(BASE_VALUES)

    const body = JSON.parse(mockFetch.mock.calls[0][1]?.body as string)
    expect(body.output_path).toBeUndefined()
    expect(body.api_key).toBeUndefined()
  })

  it('getJobs, getJob, getReport, getTree GET their respective paths', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValue(jsonResponse([]))

    await getJobs()
    await getJob('abc')
    await getReport('abc')
    await getTree('abc')

    expect(mockFetch.mock.calls.map((c) => c[0])).toEqual([
      '/api/jobs',
      '/api/jobs/abc',
      '/api/jobs/abc/report',
      '/api/jobs/abc/tree',
    ])
  })

  it('getFile URL-encodes the path query parameter', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ translated: '', source: null }))

    await getFile('abc', 'src/some file.ts')

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/jobs/abc/file?path=src%2Fsome%20file.ts',
      { headers: undefined },
    )
  })

  it('throws the server-provided detail message on a non-ok response', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce(jsonResponse({ detail: 'Input path does not exist' }, false, 400))

    await expect(getLanguages()).rejects.toThrow('Input path does not exist')
  })

  it('falls back to a generic message when the error body has no detail', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response)

    await expect(getLanguages()).rejects.toThrow('Internal Server Error')
  })
})

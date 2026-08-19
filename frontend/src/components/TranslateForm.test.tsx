import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TranslateForm } from './TranslateForm'
import * as api from '../api'
import type { Language, ProvidersResponse } from '../types'

vi.mock('../api')

const LANGUAGES: Language[] = [
  { name: 'typescript', aliases: ['ts'], has_runner: false, has_test_runner: true },
  { name: 'python', aliases: ['py'], has_runner: true, has_test_runner: true },
]

const PROVIDERS: ProvidersResponse = {
  providers: [
    { name: 'offline', models: [], freeform_model: true },
    { name: 'claude', models: ['haiku', 'sonnet', 'opus'], freeform_model: false },
    { name: 'openai-compat', models: [], freeform_model: true },
    { name: 'ollama', models: [], freeform_model: true },
  ],
  offline_pairs: ['typescript:python'],
}

async function renderLoaded() {
  vi.mocked(api.getLanguages).mockResolvedValueOnce(LANGUAGES)
  vi.mocked(api.getProviders).mockResolvedValueOnce(PROVIDERS)
  const utils = render(<TranslateForm onEstimate={vi.fn()} onTranslate={vi.fn()} busy={false} />)
  await screen.findByText('From language')
  return utils
}

describe('TranslateForm', () => {
  beforeEach(() => {
    vi.mocked(api.getLanguages).mockReset()
    vi.mocked(api.getProviders).mockReset()
  })

  it('populates the language dropdowns from /api/languages', async () => {
    await renderLoaded()
    const fromSelect = screen.getByLabelText('From language') as HTMLSelectElement
    const options = Array.from(fromSelect.options).map((o) => o.value)
    expect(options).toEqual(['typescript', 'python'])
  })

  it('populates the provider dropdown from /api/providers', async () => {
    await renderLoaded()
    const providerSelect = screen.getByLabelText('Provider') as HTMLSelectElement
    const options = Array.from(providerSelect.options).map((o) => o.value)
    expect(options).toEqual(['offline', 'claude', 'openai-compat', 'ollama'])
  })

  it('defaults from/to language to the first two languages returned', async () => {
    await renderLoaded()
    expect(screen.getByLabelText('From language')).toHaveValue('typescript')
    expect(screen.getByLabelText('To language')).toHaveValue('python')
  })

  it('shows a free-text model input when the provider is freeform (offline)', async () => {
    await renderLoaded()
    // default provider is 'offline', which has freeform_model: true
    expect(screen.getByPlaceholderText('n/a')).toBeInTheDocument()
  })

  it('switches to a model <select> when choosing a non-freeform provider', async () => {
    await renderLoaded()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'claude')

    const modelSelect = screen.getByLabelText('Model') as HTMLSelectElement
    expect(modelSelect.tagName).toBe('SELECT')
    const options = Array.from(modelSelect.options).map((o) => o.value)
    expect(options).toEqual(['haiku', 'sonnet', 'opus'])
    // handleProviderChange resets the model to the new provider's first option
    expect(modelSelect).toHaveValue('haiku')
  })

  it('shows a Base URL field only for the openai-compat provider', async () => {
    await renderLoaded()
    expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai-compat')
    expect(screen.getByLabelText('Base URL')).toBeInTheDocument()
  })

  it('hides the API key field for offline and ollama, shows it otherwise', async () => {
    await renderLoaded()
    // default provider is offline
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'ollama')
    expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'claude')
    expect(screen.getByLabelText(/API key/)).toBeInTheDocument()
  })

  it('warns when the offline provider does not support the selected language pair', async () => {
    await renderLoaded()
    await userEvent.selectOptions(screen.getByLabelText('To language'), 'typescript')
    // typescript -> typescript isn't in offline_pairs
    expect(screen.getByText(/isn't available for/)).toBeInTheDocument()
  })

  it('disables Estimate/Start until an input path is entered', async () => {
    await renderLoaded()
    expect(screen.getByRole('button', { name: 'Estimate cost' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start translation' })).toBeDisabled()

    await userEvent.type(screen.getByPlaceholderText('/path/to/repo'), '/repo')

    expect(screen.getByRole('button', { name: 'Estimate cost' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Start translation' })).toBeEnabled()
  })

  it('calls onEstimate with current form values', async () => {
    const onEstimate = vi.fn()
    vi.mocked(api.getLanguages).mockResolvedValueOnce(LANGUAGES)
    vi.mocked(api.getProviders).mockResolvedValueOnce(PROVIDERS)
    render(<TranslateForm onEstimate={onEstimate} onTranslate={vi.fn()} busy={false} />)
    await screen.findByText('From language')

    await userEvent.type(screen.getByPlaceholderText('/path/to/repo'), '/repo')
    await userEvent.click(screen.getByRole('button', { name: 'Estimate cost' }))

    expect(onEstimate).toHaveBeenCalledWith(
      expect.objectContaining({ input_path: '/repo', from_lang: 'typescript', to_lang: 'python' }),
    )
  })

  it('calls onTranslate on form submit', async () => {
    const onTranslate = vi.fn()
    vi.mocked(api.getLanguages).mockResolvedValueOnce(LANGUAGES)
    vi.mocked(api.getProviders).mockResolvedValueOnce(PROVIDERS)
    render(<TranslateForm onEstimate={vi.fn()} onTranslate={onTranslate} busy={false} />)
    await screen.findByText('From language')

    await userEvent.type(screen.getByPlaceholderText('/path/to/repo'), '/repo')
    await userEvent.click(screen.getByRole('button', { name: 'Start translation' }))

    expect(onTranslate).toHaveBeenCalledWith(
      expect.objectContaining({ input_path: '/repo' }),
    )
  })

  it('disables both action buttons while busy', async () => {
    vi.mocked(api.getLanguages).mockResolvedValueOnce(LANGUAGES)
    vi.mocked(api.getProviders).mockResolvedValueOnce(PROVIDERS)
    render(<TranslateForm onEstimate={vi.fn()} onTranslate={vi.fn()} busy={true} />)
    await screen.findByText('From language')

    expect(screen.getByRole('button', { name: 'Estimate cost' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start translation' })).toBeDisabled()
  })

  it('shows a load-error message if the initial API calls fail', async () => {
    vi.mocked(api.getLanguages).mockRejectedValueOnce(new Error('network down'))
    vi.mocked(api.getProviders).mockResolvedValueOnce(PROVIDERS)
    render(<TranslateForm onEstimate={vi.fn()} onTranslate={vi.fn()} busy={false} />)

    expect(await screen.findByText(/Failed to load form data: network down/)).toBeInTheDocument()
  })

  it('toggles the run_tests checkbox', async () => {
    await renderLoaded()
    const checkbox = screen.getByLabelText('Run tests after') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    await userEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
  })

  it('toggles the resume checkbox, defaulting to off', async () => {
    await renderLoaded()
    const checkbox = screen.getByLabelText('Resume from checkpoint') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    await userEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
  })

  it('toggles the cross-file context checkbox, defaulting to off', async () => {
    await renderLoaded()
    const checkbox = screen.getByLabelText('Cross-file context') as HTMLInputElement
    expect(checkbox.checked).toBe(false)
    await userEvent.click(checkbox)
    expect(checkbox.checked).toBe(true)
  })

  it('renders immediately with empty dropdowns, then populates once languages/providers resolve', async () => {
    let resolveProviders: (v: ProvidersResponse) => void = () => {}
    vi.mocked(api.getLanguages).mockResolvedValueOnce(LANGUAGES)
    vi.mocked(api.getProviders).mockReturnValueOnce(
      new Promise((resolve) => { resolveProviders = resolve }),
    )
    render(<TranslateForm onEstimate={vi.fn()} onTranslate={vi.fn()} busy={false} />)

    // The form isn't gated behind a loading state — it renders right away
    // with empty <select> options, filled in once the fetches resolve
    // (Promise.all([getLanguages(), getProviders()]) settles).
    expect((screen.getByLabelText('From language') as HTMLSelectElement).options).toHaveLength(0)

    resolveProviders(PROVIDERS)
    await waitFor(() =>
      expect((screen.getByLabelText('From language') as HTMLSelectElement).options).toHaveLength(2),
    )
  })
})

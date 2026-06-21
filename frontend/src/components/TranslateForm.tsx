import { useEffect, useMemo, useState } from 'react'
import { getLanguages, getProviders } from '../api'
import type { Language, ProvidersResponse, TranslateFormValues } from '../types'

const DEFAULT_VALUES: TranslateFormValues = {
  input_path: '',
  output_path: '',
  from_lang: '',
  to_lang: '',
  provider: 'offline',
  model: '',
  api_key: '',
  base_url: '',
  run_tests: false,
  translate_manifests: true,
  score_confidence: true,
}

interface Props {
  onEstimate: (values: TranslateFormValues) => void
  onTranslate: (values: TranslateFormValues) => void
  busy: boolean
}

export function TranslateForm({ onEstimate, onTranslate, busy }: Props) {
  const [languages, setLanguages] = useState<Language[]>([])
  const [providers, setProviders] = useState<ProvidersResponse | null>(null)
  const [values, setValues] = useState<TranslateFormValues>(DEFAULT_VALUES)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([getLanguages(), getProviders()])
      .then(([langs, provs]) => {
        setLanguages(langs)
        setProviders(provs)
        setValues((v) => ({
          ...v,
          from_lang: v.from_lang || langs[0]?.name || '',
          to_lang: v.to_lang || langs[1]?.name || langs[0]?.name || '',
        }))
      })
      .catch((e) => setLoadError(e.message))
  }, [])

  const currentProvider = useMemo(
    () => providers?.providers.find((p) => p.name === values.provider),
    [providers, values.provider],
  )

  const isOfflinePairSupported = useMemo(() => {
    if (values.provider !== 'offline' || !providers) return true
    return providers.offline_pairs.includes(`${values.from_lang}:${values.to_lang}`)
  }, [providers, values])

  function set<K extends keyof TranslateFormValues>(key: K, value: TranslateFormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }))
  }

  function handleProviderChange(name: string) {
    const next = providers?.providers.find((p) => p.name === name)
    set('provider', name)
    set('model', next?.models[0] ?? '')
  }

  const canSubmit = values.input_path.trim() !== '' && values.from_lang && values.to_lang && !busy

  if (loadError) {
    return <p className="error">Failed to load form data: {loadError}</p>
  }

  return (
    <form
      className="translate-form"
      onSubmit={(e) => {
        e.preventDefault()
        onTranslate(values)
      }}
    >
      <label>
        Input path
        <input
          type="text"
          placeholder="/path/to/repo"
          value={values.input_path}
          onChange={(e) => set('input_path', e.target.value)}
        />
      </label>

      <label>
        Output path (optional)
        <input
          type="text"
          placeholder="defaults to <input>_<to_lang>"
          value={values.output_path}
          onChange={(e) => set('output_path', e.target.value)}
        />
      </label>

      <div className="row">
        <label>
          From language
          <select value={values.from_lang} onChange={(e) => set('from_lang', e.target.value)}>
            {languages.map((l) => (
              <option key={l.name} value={l.name}>{l.name}</option>
            ))}
          </select>
        </label>

        <label>
          To language
          <select value={values.to_lang} onChange={(e) => set('to_lang', e.target.value)}>
            {languages.map((l) => (
              <option key={l.name} value={l.name}>{l.name}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="row">
        <label>
          Provider
          <select value={values.provider} onChange={(e) => handleProviderChange(e.target.value)}>
            {providers?.providers.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>

        <label>
          Model
          {currentProvider?.freeform_model ? (
            <input
              type="text"
              placeholder={values.provider === 'offline' ? 'n/a' : 'model id'}
              value={values.model}
              onChange={(e) => set('model', e.target.value)}
            />
          ) : (
            <select value={values.model} onChange={(e) => set('model', e.target.value)}>
              {currentProvider?.models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          )}
        </label>
      </div>

      {values.provider === 'offline' && !isOfflinePairSupported && (
        <p className="warning">
          Offline translation isn't available for {values.from_lang} → {values.to_lang}.
          Supported pairs: {providers?.offline_pairs.join(', ')}
        </p>
      )}

      {values.provider === 'openai-compat' && (
        <label>
          Base URL
          <input
            type="text"
            placeholder="https://api.example.com/v1"
            value={values.base_url}
            onChange={(e) => set('base_url', e.target.value)}
          />
        </label>
      )}

      {values.provider !== 'offline' && values.provider !== 'ollama' && (
        <label>
          API key (optional — falls back to env var)
          <input
            type="password"
            value={values.api_key}
            onChange={(e) => set('api_key', e.target.value)}
          />
        </label>
      )}

      <div className="row checkboxes">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={values.translate_manifests}
            onChange={(e) => set('translate_manifests', e.target.checked)}
          />
          Translate manifests
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={values.score_confidence}
            onChange={(e) => set('score_confidence', e.target.checked)}
          />
          Score confidence
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={values.run_tests}
            onChange={(e) => set('run_tests', e.target.checked)}
          />
          Run tests after
        </label>
      </div>

      <div className="row actions">
        <button type="button" disabled={!canSubmit} onClick={() => onEstimate(values)}>
          Estimate cost
        </button>
        <button type="submit" disabled={!canSubmit} className="primary">
          Start translation
        </button>
      </div>
    </form>
  )
}

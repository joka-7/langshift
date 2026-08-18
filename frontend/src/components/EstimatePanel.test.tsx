import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EstimatePanel } from './EstimatePanel'
import type { EstimateResult } from '../types'

const BASE_ESTIMATE: EstimateResult = {
  from_lang: 'typescript',
  to_lang: 'python',
  provider: 'claude',
  model: 'sonnet',
  file_count: 3,
  manifest_count: 1,
  input_tokens: 12000,
  output_tokens: 8000,
  estimated_cost: 0.42,
  price_label: '$3.00/$15.00 per MTok in/out',
}

describe('EstimatePanel', () => {
  it('renders the formatted cost when estimated_cost is a number', () => {
    render(
      <EstimatePanel estimate={BASE_ESTIMATE} onConfirm={vi.fn()} onCancel={vi.fn()} busy={false} />,
    )
    expect(screen.getByText('$0.4200')).toBeInTheDocument()
  })

  it('shows a placeholder instead of crashing when estimated_cost is null', () => {
    // Regression: estimated_cost used to be typed as a non-nullable `number`
    // and this component called estimate.estimated_cost.toFixed(4) directly.
    // The backend returns null for openai-compat and any provider/model pair
    // with no known pricing (agent.estimate_translation), which crashed the
    // panel with a TypeError instead of rendering.
    const estimate: EstimateResult = { ...BASE_ESTIMATE, provider: 'openai-compat', estimated_cost: null }

    render(<EstimatePanel estimate={estimate} onConfirm={vi.fn()} onCancel={vi.fn()} busy={false} />)

    expect(screen.getByText('pricing not on record')).toBeInTheDocument()
    expect(screen.queryByText(/^\$/)).not.toBeInTheDocument()
  })

  it('renders file and manifest counts, and the price label', () => {
    render(
      <EstimatePanel estimate={BASE_ESTIMATE} onConfirm={vi.fn()} onCancel={vi.fn()} busy={false} />,
    )
    expect(screen.getByText(/3 source file\(s\), 1 manifest\(s\)/)).toBeInTheDocument()
    expect(screen.getByText(/\$3\.00\/\$15\.00 per MTok in\/out/)).toBeInTheDocument()
  })

  it('calls onConfirm when "Confirm & start" is clicked', async () => {
    const onConfirm = vi.fn()
    render(
      <EstimatePanel estimate={BASE_ESTIMATE} onConfirm={onConfirm} onCancel={vi.fn()} busy={false} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel when Cancel is clicked', async () => {
    const onCancel = vi.fn()
    render(
      <EstimatePanel estimate={BASE_ESTIMATE} onConfirm={vi.fn()} onCancel={onCancel} busy={false} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('disables both buttons while busy', () => {
    render(
      <EstimatePanel estimate={BASE_ESTIMATE} onConfirm={vi.fn()} onCancel={vi.fn()} busy={true} />,
    )
    expect(screen.getByRole('button', { name: /cancel/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /confirm/i })).toBeDisabled()
  })
})

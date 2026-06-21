import type { EstimateResult } from '../types'

interface Props {
  estimate: EstimateResult
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
}

export function EstimatePanel({ estimate, onConfirm, onCancel, busy }: Props) {
  return (
    <div className="estimate-panel">
      <h3>Cost estimate</h3>
      <ul>
        <li>{estimate.from_lang} → {estimate.to_lang} via {estimate.provider}</li>
        <li>{estimate.file_count} source file(s), {estimate.manifest_count} manifest(s)</li>
        <li>~{estimate.input_tokens.toLocaleString()} input / {estimate.output_tokens.toLocaleString()} output tokens</li>
        <li>Pricing: {estimate.price_label}</li>
        <li className="cost">
          Estimated cost: <strong>${estimate.estimated_cost.toFixed(4)}</strong>
        </li>
      </ul>
      <div className="row actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="primary" onClick={onConfirm} disabled={busy}>
          Confirm &amp; start
        </button>
      </div>
    </div>
  )
}

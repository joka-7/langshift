import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// jsdom doesn't implement scrollable-element APIs; ProgressView calls
// element.scrollTo() to keep its event log pinned to the bottom.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}

// @testing-library/react's auto-cleanup relies on detecting a Jest-style
// global test framework; since this project calls describe/it/expect
// explicitly from 'vitest' rather than enabling vitest's `globals` mode,
// cleanup must be wired up manually or DOM nodes leak between tests within
// the same file (causing spurious "multiple elements found" failures).
afterEach(() => {
  cleanup()
})

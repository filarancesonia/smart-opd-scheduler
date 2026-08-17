import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { http, setDocumentHidden } from './harness'
import { installLocalStorage } from './localStorage'

installLocalStorage()

beforeEach(() => {
  localStorage.clear()
  http.reset()
  http.install()
})

afterEach(() => {
  cleanup()
  // A test that hid the document must not leave the next one paused.
  setDocumentHidden(false)
  vi.useRealTimers()
  vi.restoreAllMocks()
})

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useAction, useAsync, usePolling } from './hooks'
import { ApiError } from './api'
import { setDocumentHidden } from '../test/harness'

// Resetting document.hidden is left to the shared afterEach in test/setup.ts,
// which runs it *after* cleanup — dispatching visibilitychange while a hook is
// still mounted would fire a poll outside act().

/** Advance fake timers inside act(), so the resulting renders are committed. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useAsync', () => {
  it('resolves and clears the loading flag', async () => {
    const { result } = renderHook(() => useAsync(() => Promise.resolve('value')))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBe('value')
    expect(result.current.error).toBeNull()
  })

  it('surfaces the ApiError code alongside the message', async () => {
    const { result } = renderHook(() =>
      useAsync(() => Promise.reject(new ApiError(409, 'conflict', 'Fully booked'))),
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('Fully booked')
    expect(result.current.errorCode).toBe('conflict')
    expect(result.current.data).toBeNull()
  })

  it('refetches on reload', async () => {
    const fetcher = vi.fn().mockResolvedValue('value')
    const { result } = renderHook(() => useAsync(fetcher))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(1)

    act(() => result.current.reload())
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  })

  it('drops a response that arrives after unmount', async () => {
    const warn = vi.spyOn(console, 'error').mockImplementation(() => {})
    let settle: (value: string) => void = () => {}
    const { unmount } = renderHook(() =>
      useAsync(() => new Promise<string>((resolve) => (settle = resolve))),
    )

    unmount()
    settle('late')
    await act(async () => {
      await Promise.resolve()
    })

    // The guard in useAsync means no setState runs, so React has nothing to
    // complain about — an unguarded version would log here.
    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
  })
})

describe('usePolling', () => {
  it('fetches once immediately', async () => {
    const fetcher = vi.fn().mockResolvedValue('value')
    const { result } = renderHook(() => usePolling(fetcher, 10_000))

    await waitFor(() => expect(result.current.data).toBe('value'))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.lastUpdated).toBeInstanceOf(Date)
  })

  it('polls on the interval', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValue('value')
    renderHook(() => usePolling(fetcher, 5_000))

    await advance(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    await advance(5_000)
    expect(fetcher).toHaveBeenCalledTimes(2)

    await advance(5_000)
    expect(fetcher).toHaveBeenCalledTimes(3)
  })

  /* Regression. A corridor board or dashboard opened in a background tab used
   * to skip its very first fetch and sit on a spinner forever, because the
   * hidden-tab guard ran before anything had loaded. Only repeat ticks pause. */
  it('still performs the first fetch in a hidden tab', async () => {
    setDocumentHidden(true)
    const fetcher = vi.fn().mockResolvedValue('value')
    const { result } = renderHook(() => usePolling(fetcher, 10_000))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.data).toBe('value')
  })

  it('pauses repeat ticks while hidden, and catches up when shown again', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValue('value')
    renderHook(() => usePolling(fetcher, 5_000))

    await advance(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    setDocumentHidden(true)
    await advance(20_000)
    // Four intervals elapsed and none of them hit the server.
    expect(fetcher).toHaveBeenCalledTimes(1)

    setDocumentHidden(false)
    await advance(0)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('stops polling after unmount', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockResolvedValue('value')
    const { unmount } = renderHook(() => usePolling(fetcher, 1_000))

    await advance(0)
    unmount()
    await advance(10_000)

    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('keeps the last good data when a later poll fails', async () => {
    // Real timers here: this asserts on rendered state rather than call
    // counts, so React has to actually commit between the two polls.
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce('good')
      .mockRejectedValue(new Error('network down'))
    const { result, unmount } = renderHook(() => usePolling(fetcher, 30))

    await waitFor(() => expect(result.current.data).toBe('good'))
    await waitFor(() => expect(result.current.error).toBe('network down'))

    // A blip must not blank a board that is showing correct information.
    expect(result.current.data).toBe('good')
    // Stop the interval before the test ends, so a stray tick cannot land
    // outside act() and warn.
    unmount()
  })
})

describe('useAction', () => {
  it('tracks busy state around the call', async () => {
    const { result } = renderHook(() => useAction())
    expect(result.current.busy).toBe(false)

    let value: string | null = null
    await act(async () => {
      value = await result.current.run(() => Promise.resolve('done'))
    })

    expect(value).toBe('done')
    expect(result.current.busy).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('captures the error and returns null rather than throwing', async () => {
    const { result } = renderHook(() => useAction())

    let value: string | null = 'unset'
    await act(async () => {
      value = await result.current.run<string>(() =>
        Promise.reject(new ApiError(403, 'forbidden', 'Not allowed')),
      )
    })

    // Callers branch on null; an unhandled rejection would break the screen.
    expect(value).toBeNull()
    expect(result.current.error).toBe('Not allowed')
    expect(result.current.busy).toBe(false)
  })

  it('clears a previous error when the next call succeeds', async () => {
    const { result } = renderHook(() => useAction())

    await act(async () => {
      await result.current.run(() => Promise.reject(new Error('first failed')))
    })
    expect(result.current.error).toBe('first failed')

    await act(async () => {
      await result.current.run(() => Promise.resolve('ok'))
    })
    expect(result.current.error).toBeNull()
  })
})

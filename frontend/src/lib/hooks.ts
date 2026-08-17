import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

export type AsyncState<T> = {
  data: T | null
  error: string | null
  errorCode: string | null
  loading: boolean
  reload: () => void
}

/** Run a fetcher on mount and whenever `deps` change. */
export function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  // Keeps the effect from depending on a fresh closure every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
        setErrorCode(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setData(null)
        setError(err instanceof Error ? err.message : 'Something went wrong')
        setErrorCode(err instanceof ApiError ? err.code : null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, errorCode, loading, reload }
}

/**
 * Poll a fetcher on an interval.
 *
 * Polling pauses while the tab is hidden: a corridor board left open for a
 * week should not hammer a hospital server it is not being read from.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): AsyncState<T> & { lastUpdated: Date | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [nonce, setNonce] = useState(0)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    // The very first fetch always runs, even in a background tab. Skipping it
    // would leave a page opened out of focus spinning forever; only the
    // repeat ticks are worth pausing.
    let primed = false

    const tick = async () => {
      if (document.hidden && primed) return
      primed = true
      try {
        const result = await fetcherRef.current()
        if (cancelled) return
        setData(result)
        setError(null)
        setErrorCode(null)
        setLastUpdated(new Date())
      } catch (err: unknown) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Something went wrong')
        setErrorCode(err instanceof ApiError ? err.code : null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void tick()
    timer = window.setInterval(tick, intervalMs)

    const onVisible = () => {
      if (!document.hidden) void tick()
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, errorCode, loading, reload, lastUpdated }
}

/** Track an in-flight action (submit, call-next) with its error message. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
      return null
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, error, setError, run }
}

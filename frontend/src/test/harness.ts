/* A recording fetch stub.
 *
 * Deliberately hand-rolled rather than pulling in a mocking library: the app
 * has exactly one fetch call site, and what these tests most need to assert is
 * *which headers went out* — the bearer token on user calls, the device key on
 * kiosk and board calls. Recording the real Request gives that directly, with
 * one less dependency to break on a fresh clone.
 */

import { vi } from 'vitest'

export type RecordedRequest = {
  method: string
  url: string
  /** Path with the /api/v1 prefix stripped, e.g. "/auth/login". */
  path: string
  headers: Record<string, string>
  body: unknown
}

type Reply = { status: number; body: unknown }
type Responder = (request: RecordedRequest) => Reply

type Route = {
  method: string
  test: (path: string) => boolean
  responder: Responder
  once: boolean
  used: boolean
}

const PREFIX = '/api/v1'

function toPath(url: string): string {
  const withoutOrigin = url.replace(/^https?:\/\/[^/]+/, '')
  const [pathname] = withoutOrigin.split('?')
  return pathname.startsWith(PREFIX) ? pathname.slice(PREFIX.length) : pathname
}

class Http {
  calls: RecordedRequest[] = []
  private routes: Route[] = []

  reset() {
    this.calls = []
    this.routes = []
  }

  install() {
    globalThis.fetch = vi.fn(this.handle) as unknown as typeof fetch
  }

  /** Register a JSON response. `pattern` matches a path exactly or by prefix. */
  on(method: string, pattern: string | RegExp, body: unknown, status = 200): this {
    return this.route(method, pattern, () => ({ status, body }), false)
  }

  /** Same, but only answers the next matching request. */
  once(method: string, pattern: string | RegExp, body: unknown, status = 200): this {
    return this.route(method, pattern, () => ({ status, body }), true)
  }

  /** Register the backend's error envelope shape. */
  onError(
    method: string,
    pattern: string | RegExp,
    code: string,
    message: string,
    status = 400,
    details: Record<string, unknown> = {},
  ): this {
    return this.route(
      method,
      pattern,
      () => ({ status, body: { error: { code, message, details } } }),
      false,
    )
  }

  /** Full control, for tests that need to inspect the request first. */
  onRequest(method: string, pattern: string | RegExp, responder: Responder): this {
    return this.route(method, pattern, responder, false)
  }

  private route(
    method: string,
    pattern: string | RegExp,
    responder: Responder,
    once: boolean,
  ): this {
    const test =
      pattern instanceof RegExp
        ? (path: string) => pattern.test(path)
        : (path: string) => path === pattern || path.startsWith(pattern)
    // Newest first, so a test can override a default set up by a helper.
    this.routes.unshift({ method: method.toUpperCase(), test, responder, once, used: false })
    return this
  }

  private handle = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = normaliseHeaders(init?.headers)
    const path = toPath(url)

    const request: RecordedRequest = {
      method,
      url,
      path,
      headers,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    }
    this.calls.push(request)

    const route = this.routes.find(
      (candidate) =>
        candidate.method === method && candidate.test(path) && !(candidate.once && candidate.used),
    )

    if (!route) {
      // Loud on purpose: a silent 404 here surfaces as a confusing assertion
      // failure three layers away.
      throw new Error(`No stub registered for ${method} ${path}`)
    }
    route.used = true

    const reply = route.responder(request)
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  /** Every recorded call to a path, for asserting on headers or payloads. */
  callsTo(method: string, pattern: string): RecordedRequest[] {
    return this.calls.filter(
      (call) => call.method === method.toUpperCase() && call.path.startsWith(pattern),
    )
  }

  lastCallTo(method: string, pattern: string): RecordedRequest | undefined {
    return this.callsTo(method, pattern).at(-1)
  }
}

function normaliseHeaders(headers: HeadersInit | undefined): Record<string, string> {
  const result: Record<string, string> = {}
  if (!headers) return result
  if (headers instanceof Headers) {
    headers.forEach((value, key) => {
      result[key.toLowerCase()] = value
    })
  } else if (Array.isArray(headers)) {
    for (const [key, value] of headers) result[key.toLowerCase()] = value
  } else {
    for (const [key, value] of Object.entries(headers)) result[key.toLowerCase()] = String(value)
  }
  return result
}

export const http = new Http()

/** Force document.hidden, which jsdom otherwise pins to false. */
export function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

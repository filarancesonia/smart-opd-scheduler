/* A minimal Web Storage implementation for tests.
 *
 * Depending on the Node version, the `localStorage` global under jsdom can
 * arrive as a bare object with no methods. The app stores its session and
 * device key there, so rather than making every test defensive about it, the
 * suite installs a real one. Same behaviour on any machine a judge clones onto.
 */

class MemoryStorage implements Storage {
  private entries = new Map<string, string>()

  get length(): number {
    return this.entries.size
  }

  key(index: number): string | null {
    return [...this.entries.keys()][index] ?? null
  }

  getItem(key: string): string | null {
    return this.entries.has(String(key)) ? this.entries.get(String(key))! : null
  }

  setItem(key: string, value: string): void {
    // Web Storage coerces both sides to strings; matching that keeps a test
    // from passing on a number that would be a string in the browser.
    this.entries.set(String(key), String(value))
  }

  removeItem(key: string): void {
    this.entries.delete(String(key))
  }

  clear(): void {
    this.entries.clear()
  }

  [name: string]: unknown
}

export function installLocalStorage(): void {
  const existing = globalThis.localStorage as Partial<Storage> | undefined
  if (existing && typeof existing.clear === 'function') return

  const storage = new MemoryStorage()
  for (const target of [globalThis, globalThis.window].filter(Boolean)) {
    Object.defineProperty(target, 'localStorage', {
      configurable: true,
      writable: true,
      value: storage,
    })
  }
}

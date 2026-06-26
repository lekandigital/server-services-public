import type { ApiErrorDetails } from '../types'

export class ApiRouteMismatchError extends Error {
  status: number
  method: string
  url: string
  snippet: string
  body: string

  constructor(method: string, url: string, status: number, snippet: string) {
    super(`API route mismatch: ${method} ${url} returned HTML (${status})`)
    this.name = 'ApiRouteMismatchError'
    this.method = method
    this.url = url
    this.status = status
    this.snippet = snippet
    this.body = snippet
  }
}

export class ApiError extends Error {
  status: number
  method: string
  url: string
  body?: unknown

  constructor(message: string, details: Partial<ApiErrorDetails> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = details.status ?? 0
    this.method = details.method ?? 'GET'
    this.url = details.url ?? ''
    this.body = details.body
  }
}

type TimeoutTier = 'list' | 'metadata' | 'thumbnail' | 'cast'

const TIMEOUTS: Record<TimeoutTier, number> = {
  list: 10_000,
  metadata: 30_000,
  thumbnail: 60_000,
  cast: 120_000,
}

export interface RequestOptions {
  method?: string
  body?: unknown
  headers?: Record<string, string>
  timeout?: TimeoutTier
  signal?: AbortSignal
  silent?: boolean
}

function isJsonContentType(ct: string | null): boolean {
  return !!ct && ct.toLowerCase().includes('json')
}

export async function apiRequest<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers ?? {}),
  }

  let body: BodyInit | undefined
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  const timeoutMs = TIMEOUTS[options.timeout ?? 'list']
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const onAbort = () => controller.abort()
  if (options.signal) options.signal.addEventListener('abort', onAbort)
  const signal = controller.signal

  const url = path.startsWith('http') ? path : path

  try {
    const res = await fetch(url, { method, headers, body, signal })
    const contentType = res.headers.get('content-type')
    const text = await res.text()

    if (isJsonContentType(contentType)) {
      let data: unknown = null
      try {
        data = text ? JSON.parse(text) : null
      } catch {
        throw new ApiError('Invalid JSON response', { status: res.status, method, url, body: text.slice(0, 200) })
      }
      if (!res.ok) {
        const msg = (data as { error?: string })?.error || res.statusText || 'Request failed'
        throw new ApiError(msg, { status: res.status, method, url, body: data as string | undefined })
      }
      return data as T
    }

    const snippet = text.replace(/\s+/g, ' ').slice(0, 240)
    if ((contentType || '').toLowerCase().includes('text/html') || !res.ok || text.includes('Cannot GET') || text.includes('Cannot POST')) {
      throw new ApiRouteMismatchError(method, url, res.status, snippet)
    }

    return text as unknown as T
  } catch (err) {
    if (err instanceof ApiRouteMismatchError || err instanceof ApiError) {
      window.dispatchEvent(new CustomEvent('cast-manager-api-error', { detail: err }))
      throw err
    }
    if ((err as Error).name === 'AbortError') {
      const timeoutError = new ApiError(`Request timed out after ${timeoutMs}ms`, { method, url })
      window.dispatchEvent(new CustomEvent('cast-manager-api-error', { detail: timeoutError }))
      throw timeoutError
    }
    const networkError = new ApiError((err as Error).message || 'Network error', { method, url })
    window.dispatchEvent(new CustomEvent('cast-manager-api-error', { detail: networkError }))
    throw networkError
  } finally {
    clearTimeout(timer)
    if (options.signal) options.signal.removeEventListener('abort', onAbort)
  }
}

export function streamUrl(path: string, raw = true): string {
  const params = new URLSearchParams({ path })
  if (raw) params.set('raw', '1')
  return `/api/files/stream?${params.toString()}`
}

export function downloadUrl(path: string): string {
  return `/api/files/download?path=${encodeURIComponent(path)}`
}

export function qrcodeUrl(text: string): string {
  return `/api/qrcode?text=${encodeURIComponent(text)}`
}

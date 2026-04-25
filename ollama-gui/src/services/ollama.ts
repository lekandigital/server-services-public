import type {
  OllamaChatRequest,
  OllamaChatResponse,
  OllamaTagsResponse,
  OllamaShowResponse,
  OllamaPullResponse,
} from '@/types/ollama'

function getBaseUrl(): string {
  return localStorage.getItem('baseUrl')?.replace(/^"|"$/g, '') || '/api'
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const base = getBaseUrl()
  const res = await fetch(`${base}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!res.ok) {
    throw new Error(`Ollama API error: ${res.status} ${res.statusText}`)
  }

  return res.json() as Promise<T>
}

export async function listModels(): Promise<OllamaTagsResponse> {
  return fetchApi<OllamaTagsResponse>('/tags')
}

export async function showModel(name: string): Promise<OllamaShowResponse> {
  return fetchApi<OllamaShowResponse>('/show', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function deleteModel(name: string): Promise<void> {
  const base = getBaseUrl()
  const res = await fetch(`${base}/delete`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) {
    throw new Error(`Failed to delete model: ${res.statusText}`)
  }
}

export async function pullModel(
  name: string,
  onProgress: (progress: OllamaPullResponse) => void,
  signal?: AbortSignal,
): Promise<void> {
  const base = getBaseUrl()
  const res = await fetch(`${base}/pull`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, stream: true }),
    signal,
  })

  if (!res.ok) {
    throw new Error(`Failed to pull model: ${res.statusText}`)
  }

  const reader = res.body?.getReader()
  if (!reader) return

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    let newlineIndex = buffer.indexOf('\n')
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex).trim()
      buffer = buffer.slice(newlineIndex + 1)
      if (line) {
        try {
          onProgress(JSON.parse(line) as OllamaPullResponse)
        } catch {
          // skip malformed lines
        }
      }
      newlineIndex = buffer.indexOf('\n')
    }
  }

  const remaining = buffer.trim()
  if (remaining) {
    try {
      onProgress(JSON.parse(remaining) as OllamaPullResponse)
    } catch {
      // skip
    }
  }
}

export async function* streamChat(
  request: OllamaChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<OllamaChatResponse> {
  const base = getBaseUrl()
  const res = await fetch(`${base}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...request, stream: true }),
    signal,
  })

  if (!res.ok) {
    throw new Error(`Ollama chat error: ${res.status} ${res.statusText}`)
  }

  const reader = res.body?.getReader()
  if (!reader) return

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    let newlineIndex = buffer.indexOf('\n')
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex).trim()
      buffer = buffer.slice(newlineIndex + 1)
      if (line) {
        try {
          yield JSON.parse(line) as OllamaChatResponse
        } catch {
          // skip malformed lines
        }
      }
      newlineIndex = buffer.indexOf('\n')
    }
  }

  const remaining = buffer.trim()
  if (remaining) {
    try {
      yield JSON.parse(remaining) as OllamaChatResponse
    } catch {
      // skip
    }
  }
}

export async function checkConnection(): Promise<boolean> {
  try {
    await listModels()
    return true
  } catch {
    return false
  }
}

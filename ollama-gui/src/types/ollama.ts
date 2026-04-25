export interface OllamaModel {
  name: string
  model: string
  modified_at: string
  size: number
  digest: string
  details: OllamaModelDetails
}

export interface OllamaModelDetails {
  parent_model: string
  format: string
  family: string
  families: string[]
  parameter_size: string
  quantization_level: string
}

export interface OllamaChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  images?: string[]
}

export interface OllamaChatRequest {
  model: string
  messages: OllamaChatMessage[]
  stream?: boolean
  options?: Record<string, unknown>
}

export interface OllamaChatResponse {
  model: string
  created_at: string
  message: OllamaChatMessage
  done: boolean
  total_duration?: number
  load_duration?: number
  prompt_eval_count?: number
  prompt_eval_duration?: number
  eval_count?: number
  eval_duration?: number
}

export interface OllamaTagsResponse {
  models: OllamaModel[]
}

export interface OllamaShowResponse {
  modelfile: string
  parameters: string
  template: string
  details: OllamaModelDetails
  model_info: Record<string, unknown>
}

export interface OllamaPullResponse {
  status: string
  digest?: string
  total?: number
  completed?: number
}

export type ConnectionStatus = 'connected' | 'disconnected' | 'connecting'

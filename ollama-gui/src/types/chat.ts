export interface Chat {
  id?: number
  name: string
  model: string
  createdAt: Date
  lastMessageAt?: Date
  folderId?: number
  pinned?: boolean
  archived?: boolean
  tags?: string[]
}

export interface Message {
  id?: number
  chatId: number
  role: 'user' | 'assistant' | 'system'
  content: string
  images?: string[]
  meta?: MessageMeta
  context?: number[]
  createdAt: Date
  parentId?: number
  bookmarked?: boolean
  branchId?: string
}

export interface MessageMeta {
  model?: string
  totalDuration?: number
  loadDuration?: number
  promptEvalCount?: number
  promptEvalDuration?: number
  evalCount?: number
  evalDuration?: number
}

export interface Folder {
  id?: number
  name: string
  parentId?: number
  order: number
  createdAt: Date
}

export interface Prompt {
  id?: number
  title: string
  content: string
  category?: string
  createdAt: Date
}

export interface Snippet {
  id?: number
  title: string
  content: string
  tags?: string[]
  messageId?: number
  createdAt: Date
}

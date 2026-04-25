import Dexie, { type EntityTable } from 'dexie'
import type { Chat, Message, Folder, Prompt, Snippet } from '@/types/chat'

class OllamaGuiDatabase extends Dexie {
  chats!: EntityTable<Chat, 'id'>
  messages!: EntityTable<Message, 'id'>
  folders!: EntityTable<Folder, 'id'>
  prompts!: EntityTable<Prompt, 'id'>
  snippets!: EntityTable<Snippet, 'id'>

  constructor() {
    super('ChatDatabase')

    // v10: Original schema (preserved for migration compatibility)
    this.version(10).stores({
      chats: '++id, name, model, createdAt',
      messages: '++id, chatId, role, content, meta, context, createdAt',
      config: '++id, model, systemPrompt, createdAt',
    })

    // v11: Extended schema with new features
    this.version(11)
      .stores({
        chats:
          '++id, name, model, createdAt, folderId, pinned, archived, lastMessageAt',
        messages:
          '++id, chatId, role, content, meta, context, createdAt, parentId, bookmarked, branchId',
        folders: '++id, name, parentId, order, createdAt',
        prompts: '++id, title, content, category, createdAt',
        snippets: '++id, title, content, tags, messageId, createdAt',
        config: '++id, model, systemPrompt, createdAt',
      })
      .upgrade((tx) => {
        return tx
          .table('chats')
          .toCollection()
          .modify((chat) => {
            if (!chat.lastMessageAt) {
              chat.lastMessageAt = chat.createdAt
            }
            if (chat.pinned === undefined) {
              chat.pinned = false
            }
            if (chat.archived === undefined) {
              chat.archived = false
            }
          })
      })
  }
}

export const db = new OllamaGuiDatabase()

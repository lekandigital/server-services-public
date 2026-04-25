import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { db } from '@/services/database'
import { streamChat } from '@/services/ollama'
import { useModelStore } from './modelStore'
import { useSettingsStore } from './settingsStore'
import type { Chat, Message, MessageMeta } from '@/types/chat'
import type { OllamaChatMessage } from '@/types/ollama'

export const useChatStore = defineStore('chat', () => {
  const chats = ref<Chat[]>([])
  const activeChatId = ref<number | null>(null)
  const messages = ref<Message[]>([])
  const isStreaming = ref(false)
  const streamingContent = ref('')
  const abortController = ref<AbortController | null>(null)
  const systemPrompts = ref<Map<string, string>>(new Map())
  const defaultSystemPrompt = ref('')

  const activeChat = computed(() =>
    chats.value.find((c) => c.id === activeChatId.value),
  )

  const sortedChats = computed(() => {
    const pinned = chats.value
      .filter((c) => c.pinned && !c.archived)
      .sort(
        (a, b) =>
          (b.lastMessageAt ?? b.createdAt).getTime() -
          (a.lastMessageAt ?? a.createdAt).getTime(),
      )

    const unpinned = chats.value
      .filter((c) => !c.pinned && !c.archived)
      .sort(
        (a, b) =>
          (b.lastMessageAt ?? b.createdAt).getTime() -
          (a.lastMessageAt ?? a.createdAt).getTime(),
      )

    return [...pinned, ...unpinned]
  })

  const hasMessages = computed(() => messages.value.length > 0)

  async function loadChats() {
    chats.value = await db.chats.orderBy('createdAt').reverse().toArray()
  }

  async function loadMessages(chatId: number) {
    messages.value = await db.messages
      .where('chatId')
      .equals(chatId)
      .sortBy('createdAt')
  }

  async function selectChat(chatId: number) {
    activeChatId.value = chatId
    await loadMessages(chatId)
  }

  async function createChat(name?: string): Promise<number> {
    const modelStore = useModelStore()
    const model = modelStore.currentModel

    const chat: Chat = {
      name: name || 'New Chat',
      model: model === 'none' ? '' : model,
      createdAt: new Date(),
      lastMessageAt: new Date(),
      pinned: false,
      archived: false,
    }

    const id = await db.chats.add(chat)
    chat.id = id as number
    chats.value.unshift(chat)
    await selectChat(id as number)
    return id as number
  }

  async function deleteChat(chatId: number) {
    await db.messages.where('chatId').equals(chatId).delete()
    await db.chats.delete(chatId)
    chats.value = chats.value.filter((c) => c.id !== chatId)

    if (activeChatId.value === chatId) {
      activeChatId.value = chats.value[0]?.id ?? null
      if (activeChatId.value) {
        await loadMessages(activeChatId.value)
      } else {
        messages.value = []
      }
    }
  }

  async function renameChat(chatId: number, name: string) {
    await db.chats.update(chatId, { name })
    const chat = chats.value.find((c) => c.id === chatId)
    if (chat) chat.name = name
  }

  async function switchModel(chatId: number, model: string) {
    await db.chats.update(chatId, { model })
    const chat = chats.value.find((c) => c.id === chatId)
    if (chat) chat.model = model
    const modelStore = useModelStore()
    modelStore.currentModel = model
  }

  async function togglePin(chatId: number) {
    const chat = chats.value.find((c) => c.id === chatId)
    if (!chat) return
    chat.pinned = !chat.pinned
    await db.chats.update(chatId, { pinned: chat.pinned })
  }

  async function addMessage(
    chatId: number,
    role: Message['role'],
    content: string,
    images?: string[],
  ): Promise<Message> {
    const msg: Message = {
      chatId,
      role,
      content,
      images,
      createdAt: new Date(),
    }

    const id = await db.messages.add(msg)
    msg.id = id as number
    messages.value.push(msg)
    await db.chats.update(chatId, { lastMessageAt: new Date() })

    return msg
  }

  function buildContext(): OllamaChatMessage[] {
    const settings = useSettingsStore()
    const chat = activeChat.value
    if (!chat) return []

    const contextMessages: OllamaChatMessage[] = []

    // Add system prompt if present
    const systemPrompt =
      systemPrompts.value.get(chat.model) || defaultSystemPrompt.value
    if (systemPrompt) {
      contextMessages.push({ role: 'system', content: systemPrompt })
    }

    // Get last N messages for context
    const recentMessages = messages.value.slice(-settings.historyLength)
    for (const msg of recentMessages) {
      if (msg.role === 'system' && !settings.showSystemMessages) continue
      const chatMsg: OllamaChatMessage = { role: msg.role, content: msg.content }
      if (msg.images?.length) {
        chatMsg.images = msg.images
      }
      contextMessages.push(chatMsg)
    }

    return contextMessages
  }

  async function sendMessage(content: string, images?: string[]) {
    const chat = activeChat.value
    if (!chat?.id || !chat.model || isStreaming.value) return

    await addMessage(chat.id, 'user', content, images)

    isStreaming.value = true
    streamingContent.value = ''
    abortController.value = new AbortController()

    const contextMessages = buildContext()
    let meta: MessageMeta = { model: chat.model }
    const startTime = Date.now()

    try {
      for await (const chunk of streamChat(
        { model: chat.model, messages: contextMessages },
        abortController.value.signal,
      )) {
        if (chunk.message?.content) {
          streamingContent.value += chunk.message.content
        }

        if (chunk.done) {
          meta = {
            model: chunk.model,
            totalDuration: chunk.total_duration,
            loadDuration: chunk.load_duration,
            promptEvalCount: chunk.prompt_eval_count,
            promptEvalDuration: chunk.prompt_eval_duration,
            evalCount: chunk.eval_count,
            evalDuration: chunk.eval_duration,
          }
        }
      }

      if (streamingContent.value) {
        const msg: Message = {
          chatId: chat.id,
          role: 'assistant',
          content: streamingContent.value,
          meta,
          createdAt: new Date(),
        }
        const id = await db.messages.add(msg)
        msg.id = id as number
        messages.value.push(msg)
        await db.chats.update(chat.id, { lastMessageAt: new Date() })
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // User cancelled — save partial response if any
        if (streamingContent.value) {
          const msg: Message = {
            chatId: chat.id,
            role: 'assistant',
            content: streamingContent.value,
            meta: { ...meta, totalDuration: Date.now() - startTime },
            createdAt: new Date(),
          }
          const id = await db.messages.add(msg)
          msg.id = id as number
          messages.value.push(msg)
        }
      } else {
        console.error('Streaming error:', err)
      }
    } finally {
      isStreaming.value = false
      streamingContent.value = ''
      abortController.value = null
    }
  }

  function abortStreaming() {
    abortController.value?.abort()
  }

  async function regenerateLastResponse() {
    if (isStreaming.value || messages.value.length < 2) return

    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg.role !== 'assistant' || !lastMsg.id) return

    // Remove the last assistant message
    await db.messages.delete(lastMsg.id)
    messages.value.pop()

    // Get the last user message content and resend
    const lastUserMsg = [...messages.value]
      .reverse()
      .find((m) => m.role === 'user')
    if (!lastUserMsg) return

    // Rebuild context and stream again
    const chat = activeChat.value
    if (!chat?.id || !chat.model) return

    isStreaming.value = true
    streamingContent.value = ''
    abortController.value = new AbortController()

    const contextMessages = buildContext()
    let meta: MessageMeta = { model: chat.model }

    try {
      for await (const chunk of streamChat(
        { model: chat.model, messages: contextMessages },
        abortController.value.signal,
      )) {
        if (chunk.message?.content) {
          streamingContent.value += chunk.message.content
        }
        if (chunk.done) {
          meta = {
            model: chunk.model,
            totalDuration: chunk.total_duration,
            loadDuration: chunk.load_duration,
            promptEvalCount: chunk.prompt_eval_count,
            promptEvalDuration: chunk.prompt_eval_duration,
            evalCount: chunk.eval_count,
            evalDuration: chunk.eval_duration,
          }
        }
      }

      if (streamingContent.value) {
        const msg: Message = {
          chatId: chat.id,
          role: 'assistant',
          content: streamingContent.value,
          meta,
          createdAt: new Date(),
        }
        const id = await db.messages.add(msg)
        msg.id = id as number
        messages.value.push(msg)
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        console.error('Regeneration error:', err)
      }
    } finally {
      isStreaming.value = false
      streamingContent.value = ''
      abortController.value = null
    }
  }

  async function addSystemMessage(chatId: number, content: string) {
    await addMessage(chatId, 'system', content)
  }

  async function deleteMessage(messageId: number) {
    await db.messages.delete(messageId)
    messages.value = messages.value.filter((m) => m.id !== messageId)
  }

  async function editMessage(messageId: number, content: string) {
    await db.messages.update(messageId, { content })
    const msg = messages.value.find((m) => m.id === messageId)
    if (msg) msg.content = content
  }

  async function clearChat(chatId: number) {
    await db.messages.where('chatId').equals(chatId).delete()
    if (activeChatId.value === chatId) {
      messages.value = []
    }
  }

  async function exportChats(): Promise<string> {
    const allChats = await db.chats.toArray()
    const allMessages = await db.messages.toArray()
    return JSON.stringify({ chats: allChats, messages: allMessages }, null, 2)
  }

  async function importChats(json: string) {
    const data = JSON.parse(json) as {
      chats: Chat[]
      messages: Message[]
    }
    await db.chats.bulkAdd(data.chats)
    await db.messages.bulkAdd(data.messages)
    await loadChats()
  }

  async function wipeAllData() {
    await db.messages.clear()
    await db.chats.clear()
    chats.value = []
    messages.value = []
    activeChatId.value = null
  }

  async function duplicateChat(chatId: number) {
    const source = chats.value.find((c) => c.id === chatId)
    if (!source) return

    const sourceMessages = await db.messages
      .where('chatId')
      .equals(chatId)
      .toArray()

    const newChat: Chat = {
      name: `${source.name} (copy)`,
      model: source.model,
      createdAt: new Date(),
      lastMessageAt: new Date(),
      pinned: false,
      archived: false,
      tags: source.tags ? [...source.tags] : undefined,
    }

    const newId = (await db.chats.add(newChat)) as number
    newChat.id = newId

    const newMessages = sourceMessages.map((m) => ({
      ...m,
      id: undefined,
      chatId: newId,
      createdAt: new Date(m.createdAt),
    }))
    await db.messages.bulkAdd(newMessages)

    chats.value.unshift(newChat)
    await selectChat(newId)
  }

  async function addTag(chatId: number, tag: string) {
    const chat = chats.value.find((c) => c.id === chatId)
    if (!chat) return
    const tags = chat.tags ? [...chat.tags] : []
    if (!tags.includes(tag)) {
      tags.push(tag)
      chat.tags = tags
      await db.chats.update(chatId, { tags })
    }
  }

  async function removeTag(chatId: number, tag: string) {
    const chat = chats.value.find((c) => c.id === chatId)
    if (!chat?.tags) return
    chat.tags = chat.tags.filter((t) => t !== tag)
    await db.chats.update(chatId, { tags: chat.tags })
  }

  async function toggleBookmark(messageId: number) {
    const msg = messages.value.find((m) => m.id === messageId)
    if (!msg) return
    msg.bookmarked = !msg.bookmarked
    await db.messages.update(messageId, { bookmarked: msg.bookmarked })
  }

  const bookmarkedMessages = computed(() =>
    messages.value.filter((m) => m.bookmarked),
  )

  const allTags = computed(() => {
    const tagSet = new Set<string>()
    for (const c of chats.value) {
      if (c.tags) c.tags.forEach((t) => tagSet.add(t))
    }
    return Array.from(tagSet).sort()
  })

  async function searchAllChats(query: string): Promise<Array<{ chat: Chat; message: Message }>> {
    if (!query.trim()) return []
    const q = query.toLowerCase()
    const allMessages = await db.messages.toArray()
    const results: Array<{ chat: Chat; message: Message }> = []

    for (const msg of allMessages) {
      if (msg.content.toLowerCase().includes(q)) {
        const chat = chats.value.find((c) => c.id === msg.chatId)
        if (chat) {
          results.push({ chat, message: msg })
        }
      }
    }
    return results.slice(0, 50)
  }

  function setSystemPrompt(model: string, prompt: string) {
    systemPrompts.value.set(model, prompt)
    localStorage.setItem(
      'systemPrompts',
      JSON.stringify(Object.fromEntries(systemPrompts.value)),
    )
  }

  function loadSystemPrompts() {
    try {
      const stored = localStorage.getItem('systemPrompts')
      if (stored) {
        const parsed = JSON.parse(stored) as Record<string, string>
        systemPrompts.value = new Map(Object.entries(parsed))
      }
      const defaultPrompt = localStorage.getItem('defaultSystemPrompt')
      if (defaultPrompt) {
        defaultSystemPrompt.value = JSON.parse(defaultPrompt) as string
      }
    } catch {
      // ignore parse errors
    }
  }

  return {
    chats,
    activeChatId,
    messages,
    activeChat,
    sortedChats,
    hasMessages,
    isStreaming,
    streamingContent,
    systemPrompts,
    defaultSystemPrompt,
    loadChats,
    loadMessages,
    selectChat,
    createChat,
    deleteChat,
    renameChat,
    switchModel,
    togglePin,
    sendMessage,
    abortStreaming,
    regenerateLastResponse,
    addSystemMessage,
    deleteMessage,
    editMessage,
    clearChat,
    exportChats,
    importChats,
    wipeAllData,
    setSystemPrompt,
    loadSystemPrompts,
    duplicateChat,
    addTag,
    removeTag,
    toggleBookmark,
    bookmarkedMessages,
    allTags,
    searchAllChats,
  }
})

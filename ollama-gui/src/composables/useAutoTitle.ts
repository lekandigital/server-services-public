import { watch } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { streamChat } from '@/services/ollama'

export function useAutoTitle() {
  const chat = useChatStore()
  const settings = useSettingsStore()

  watch(
    () => chat.messages.length,
    async (newLen, oldLen) => {
      if (!settings.autoTitle) return
      if (newLen < 2 || oldLen >= 2) return

      const activeChat = chat.activeChat
      if (!activeChat?.id || !activeChat.model) return
      if (activeChat.name !== 'New Chat') return

      const userMsg = chat.messages.find((m) => m.role === 'user')
      const aiMsg = chat.messages.find((m) => m.role === 'assistant')
      if (!userMsg || !aiMsg) return

      try {
        let title = ''
        for await (const chunk of streamChat({
          model: activeChat.model,
          messages: [
            {
              role: 'system',
              content:
                'Generate a concise 3-6 word title for this conversation. Reply with ONLY the title, no quotes, no punctuation at the end.',
            },
            { role: 'user', content: userMsg.content },
            {
              role: 'assistant',
              content: aiMsg.content.slice(0, 200),
            },
            {
              role: 'user',
              content: 'What is a good short title for this conversation?',
            },
          ],
        })) {
          if (chunk.message?.content) {
            title += chunk.message.content
          }
        }

        title = title.trim().replace(/^["']|["']$/g, '')
        if (title && title.length > 0 && title.length < 60) {
          await chat.renameChat(activeChat.id, title)
        }
      } catch {
        // Silent fail for auto-title — non-critical feature
      }
    },
  )
}

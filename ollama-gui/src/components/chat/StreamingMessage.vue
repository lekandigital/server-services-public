<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import Markdown from '@/components/markdown/Markdown.vue'
import ThinkBlock from './ThinkBlock.vue'

const chat = useChatStore()
const settings = useSettingsStore()

const streamParsed = computed(() => {
  const content = chat.streamingContent
  if (!content) return { thinking: null, response: '', isThinking: false }

  const thinkStart = content.indexOf('<think>')
  if (thinkStart === -1) {
    return { thinking: null, response: content, isThinking: false }
  }

  const thinkEnd = content.indexOf('</think>')
  if (thinkEnd === -1) {
    // Still thinking — unclosed <think> tag
    const thinkContent = content.slice(thinkStart + 7).trim()
    return { thinking: thinkContent, response: '', isThinking: true }
  }

  // Thinking complete, response streaming
  const thinkContent = content.slice(thinkStart + 7, thinkEnd).trim()
  const response = content.slice(thinkEnd + 8).trimStart()
  return { thinking: thinkContent, response, isThinking: false }
})
</script>

<template>
  <div class="flex gap-3">
    <!-- Avatar -->
    <div class="flex-shrink-0 pt-1">
      <div class="flex h-7 w-7 items-center justify-center rounded-full bg-surface-3">
        <img src="/logo.png" alt="AI" class="h-4 w-4" />
      </div>
    </div>

    <!-- Streaming content -->
    <div class="min-w-0 flex-1">
      <div class="px-0 py-1">
        <!-- Think block (active or completed) -->
        <ThinkBlock
          v-if="streamParsed.thinking !== null"
          :content="streamParsed.thinking"
          :is-streaming="streamParsed.isThinking"
        />

        <!-- Response content (after thinking, or when no thinking) -->
        <template v-if="streamParsed.response || streamParsed.thinking === null">
          <div class="prose-chat text-sm text-text-primary">
            <Markdown
              v-if="settings.enableMarkdown"
              :content="streamParsed.thinking === null ? chat.streamingContent : streamParsed.response"
            />
            <div v-else class="whitespace-pre-wrap break-words">
              {{ streamParsed.thinking === null ? chat.streamingContent : streamParsed.response }}
            </div>
          </div>
        </template>

        <!-- Typing cursor -->
        <span
          v-if="!streamParsed.isThinking"
          class="inline-block h-4 w-0.5 animate-pulse bg-accent-soft"
        />
      </div>
    </div>
  </div>
</template>

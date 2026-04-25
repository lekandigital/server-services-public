<script setup lang="ts">
import { ref, computed } from 'vue'
import { formatDistanceToNow } from 'date-fns'
import { IconCopy, IconCheck, IconBookmark, IconBookmarkFilled } from '@tabler/icons-vue'
import type { Message } from '@/types/chat'
import { useSettingsStore } from '@/stores/settingsStore'
import { useChatStore } from '@/stores/chatStore'
import Markdown from '@/components/markdown/Markdown.vue'
import ThinkBlock from './ThinkBlock.vue'
import ResponseMetrics from './ResponseMetrics.vue'

const props = defineProps<{
  message: Message
}>()

const settings = useSettingsStore()
const chat = useChatStore()
const showTime = ref(false)
const copied = ref(false)

// Parse think blocks
const parsed = computed(() => {
  const content = props.message.content
  const thinkRegex = /<think>([\s\S]*?)<\/think>/g
  const parts: Array<{ type: 'text' | 'think'; content: string }> = []

  let lastIndex = 0
  let match: RegExpExecArray | null = null

  while ((match = thinkRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: content.slice(lastIndex, match.index) })
    }
    parts.push({ type: 'think', content: match[1].trim() })
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < content.length) {
    parts.push({ type: 'text', content: content.slice(lastIndex) })
  }

  return parts
})

async function copyMessage() {
  try {
    await navigator.clipboard.writeText(props.message.content)
  } catch {
    // Fallback for non-HTTPS contexts (e.g. LAN access)
    const ta = document.createElement('textarea')
    ta.value = props.message.content
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div
    class="group flex gap-3"
    @mouseenter="showTime = true"
    @mouseleave="showTime = false"
  >
    <!-- Avatar -->
    <div class="flex-shrink-0 pt-1">
      <div class="flex h-7 w-7 items-center justify-center rounded-full bg-surface-3">
        <img src="/logo.png" alt="AI" class="h-4 w-4" />
      </div>
    </div>

    <!-- Content -->
    <div class="min-w-0 flex-1 space-y-2">
      <div class="relative px-0 py-1">
        <!-- Action buttons -->
        <div class="absolute right-0 top-1 flex gap-0.5 opacity-0 transition-all group-hover:opacity-100">
          <button
            v-if="message.id"
            @click="chat.toggleBookmark(message.id)"
            class="rounded-md p-1 text-text-muted hover:bg-surface-3 hover:text-accent"
            :title="message.bookmarked ? 'Remove bookmark' : 'Bookmark'"
          >
            <IconBookmarkFilled v-if="message.bookmarked" :size="14" class="text-accent" />
            <IconBookmark v-else :size="14" />
          </button>
          <button
            @click="copyMessage"
            class="rounded-md p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
            title="Copy message"
          >
            <IconCheck v-if="copied" :size="14" class="text-success" />
            <IconCopy v-else :size="14" />
          </button>
        </div>

        <!-- Message parts -->
        <template v-for="(part, i) in parsed" :key="i">
          <ThinkBlock v-if="part.type === 'think'" :content="part.content" />
          <div v-else class="prose-chat text-sm text-text-primary">
            <Markdown v-if="settings.enableMarkdown" :content="part.content" />
            <div v-else class="whitespace-pre-wrap break-words">{{ part.content }}</div>
          </div>
        </template>
      </div>

      <!-- Metrics -->
      <ResponseMetrics
        v-if="settings.showMetrics && message.meta"
        :meta="message.meta"
      />

      <!-- Timestamp -->
      <Transition name="fade">
        <div v-if="showTime" class="text-2xs text-text-muted">
          {{ formatDistanceToNow(message.createdAt, { addSuffix: true }) }}
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active {
  transition: opacity var(--transition-fast);
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

:deep(.prose-chat) {
  @apply leading-relaxed;
}
:deep(.prose-chat p) {
  @apply my-2 first:mt-0 last:mb-0;
}
:deep(.prose-chat pre) {
  @apply my-3 overflow-x-auto rounded-lg bg-surface-0 p-3;
}
:deep(.prose-chat code:not(pre code)) {
  @apply rounded bg-surface-0 px-1.5 py-0.5 text-xs text-accent-soft;
}
:deep(.prose-chat ul, .prose-chat ol) {
  @apply my-2 pl-5;
}
:deep(.prose-chat li) {
  @apply my-0.5;
}
:deep(.prose-chat a) {
  @apply text-accent underline;
}
:deep(.prose-chat blockquote) {
  @apply my-2 border-l-2 border-accent pl-3 italic text-text-secondary;
}
:deep(.prose-chat table) {
  @apply my-3 w-full border-collapse text-sm;
}
:deep(.prose-chat th, .prose-chat td) {
  @apply border border-[var(--color-border)] px-3 py-1.5 text-left;
}
:deep(.prose-chat th) {
  @apply bg-surface-3 font-medium;
}
:deep(.prose-chat h1, .prose-chat h2, .prose-chat h3) {
  @apply my-3 font-semibold text-text-primary first:mt-0;
}
</style>

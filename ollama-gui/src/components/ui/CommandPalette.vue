<script setup lang="ts">
import { ref, watch } from 'vue'
import { IconSearch, IconMessage, IconX } from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import type { Chat, Message } from '@/types/chat'

const chat = useChatStore()
const ui = useUiStore()

const query = ref('')
const results = ref<Array<{ chat: Chat; message: Message }>>([])
const isSearching = ref(false)
const inputRef = ref<HTMLInputElement>()

let debounceTimer: ReturnType<typeof setTimeout> | null = null

watch(query, (q) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!q.trim()) {
    results.value = []
    return
  }
  isSearching.value = true
  debounceTimer = setTimeout(async () => {
    results.value = await chat.searchAllChats(q)
    isSearching.value = false
  }, 300)
})

watch(() => ui.commandPaletteOpen, (open) => {
  if (open) {
    query.value = ''
    results.value = []
    setTimeout(() => inputRef.value?.focus(), 100)
  }
})

function selectResult(chatId: number) {
  chat.selectChat(chatId)
  ui.commandPaletteOpen = false
}

function close() {
  ui.commandPaletteOpen = false
}

function highlightMatch(text: string): string {
  if (!query.value.trim()) return escapeHtml(text)
  const q = query.value.trim()
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx === -1) return escapeHtml(text)
  const before = text.slice(0, idx)
  const match = text.slice(idx, idx + q.length)
  const after = text.slice(idx + q.length)
  return `${escapeHtml(before)}<mark class="bg-accent/30 text-text-primary">${escapeHtml(match)}</mark>${escapeHtml(after)}`
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function truncateAround(text: string, maxLen = 120): string {
  if (!query.value.trim()) return text.slice(0, maxLen)
  const q = query.value.trim().toLowerCase()
  const idx = text.toLowerCase().indexOf(q)
  if (idx === -1) return text.slice(0, maxLen)
  const start = Math.max(0, idx - 40)
  const end = Math.min(text.length, idx + q.length + 60)
  let snippet = text.slice(start, end)
  if (start > 0) snippet = '...' + snippet
  if (end < text.length) snippet = snippet + '...'
  return snippet
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="ui.commandPaletteOpen"
      class="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 pt-[10vh]"
      @click.self="close"
      @keydown.escape="close"
    >
      <div class="w-full max-w-xl animate-slide-up rounded-xl bg-surface-1 shadow-2xl">
        <!-- Search input -->
        <div class="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
          <IconSearch :size="18" class="text-text-muted" />
          <input
            ref="inputRef"
            v-model="query"
            type="text"
            placeholder="Search all chats..."
            class="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted focus:outline-none"
          />
          <button @click="close" class="rounded p-1 text-text-muted hover:text-text-primary">
            <IconX :size="16" />
          </button>
        </div>

        <!-- Results -->
        <div class="max-h-[50vh] overflow-y-auto">
          <div v-if="isSearching" class="px-4 py-8 text-center text-sm text-text-muted">
            Searching...
          </div>
          <div v-else-if="query.trim() && !results.length" class="px-4 py-8 text-center text-sm text-text-muted">
            No results found
          </div>
          <div v-else-if="!query.trim()" class="px-4 py-8 text-center text-sm text-text-muted">
            Type to search across all conversations
          </div>
          <button
            v-for="(r, i) in results"
            :key="i"
            @click="selectResult(r.chat.id!)"
            class="flex w-full items-start gap-3 border-b border-[var(--color-border)] px-4 py-3 text-left transition-colors last:border-0 hover:bg-surface-2"
          >
            <IconMessage :size="16" class="mt-0.5 flex-shrink-0 text-text-muted" />
            <div class="min-w-0 flex-1">
              <div class="text-xs font-medium text-text-secondary">{{ r.chat.name }}</div>
              <div
                class="mt-0.5 text-xs text-text-muted"
                v-html="highlightMatch(truncateAround(r.message.content))"
              />
            </div>
            <span class="flex-shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-2xs text-text-muted">
              {{ r.message.role }}
            </span>
          </button>
        </div>

        <!-- Footer -->
        <div class="border-t border-[var(--color-border)] px-4 py-2 text-2xs text-text-muted">
          <kbd class="rounded bg-surface-3 px-1 py-0.5 font-mono">Esc</kbd> to close
          &middot;
          <kbd class="rounded bg-surface-3 px-1 py-0.5 font-mono">Cmd+K</kbd> to toggle
        </div>
      </div>
    </div>
  </Teleport>
</template>

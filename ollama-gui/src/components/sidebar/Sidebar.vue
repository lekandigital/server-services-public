<script setup lang="ts">
import { ref } from 'vue'
import {
  IconPlus, IconSettings, IconMoon, IconSun,
  IconSearch, IconMessage, IconChartBar,
} from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useUiStore } from '@/stores/uiStore'
import ChatList from './ChatList.vue'
import StatsPanel from '@/components/ui/StatsPanel.vue'

const chat = useChatStore()
const settings = useSettingsStore()
const ui = useUiStore()
const searchQuery = ref('')
const statsOpen = ref(false)

async function handleNewChat() {
  await chat.createChat()
  if (ui.isMobile) ui.closeSidebar()
}
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- Header -->
    <div class="flex items-center gap-2 border-b border-[var(--color-border)] p-3">
      <button
        @click="handleNewChat"
        class="flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-soft"
      >
        <IconPlus :size="18" />
        New Chat
      </button>
    </div>

    <!-- Search -->
    <div class="border-b border-[var(--color-border)] px-3 py-2">
      <div class="relative">
        <IconSearch :size="16" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Search chats..."
          class="w-full rounded-md border border-[var(--color-border)] bg-surface-2 py-1.5 pl-8 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>
    </div>

    <!-- Tag filter -->
    <div v-if="chat.allTags.length" class="flex flex-wrap gap-1 border-b border-[var(--color-border)] px-3 py-2">
      <button
        v-for="tag in chat.allTags"
        :key="tag"
        class="rounded-full bg-surface-2 px-2 py-0.5 text-2xs text-text-secondary transition-colors hover:bg-accent/20 hover:text-accent"
      >
        {{ tag }}
      </button>
    </div>

    <!-- Chat list -->
    <div class="flex-1 overflow-y-auto">
      <ChatList :search-query="searchQuery" />
    </div>

    <!-- Footer -->
    <div class="flex items-center gap-1 border-t border-[var(--color-border)] p-2">
      <button
        @click="settings.toggleTheme()"
        class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
        :title="settings.theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
      >
        <IconSun v-if="settings.theme === 'dark'" :size="18" />
        <IconMoon v-else :size="18" />
      </button>
      <button
        @click="ui.toggleSystemPrompt()"
        class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
        title="System Prompt"
      >
        <IconMessage :size="18" />
      </button>
      <button
        @click="statsOpen = true"
        class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
        title="Statistics"
      >
        <IconChartBar :size="18" />
      </button>
      <button
        @click="ui.toggleSettings()"
        class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
        title="Settings"
      >
        <IconSettings :size="18" />
      </button>
      <div class="flex-1" />
      <span class="text-2xs text-text-muted">Ollama GUI v2</span>
    </div>

    <StatsPanel :open="statsOpen" @close="statsOpen = false" />
  </div>
</template>

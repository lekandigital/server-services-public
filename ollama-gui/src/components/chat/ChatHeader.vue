<script setup lang="ts">
import { IconMenu2, IconCpu } from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import ModelSelector from '@/components/settings/ModelSelector.vue'

const chat = useChatStore()
const ui = useUiStore()
</script>

<template>
  <header
    class="flex items-center gap-2 border-b border-[var(--color-border)] bg-surface-1 px-3 py-2"
  >
    <!-- Hamburger (mobile/tablet when sidebar hidden) -->
    <button
      v-if="!ui.sidebarOpen || ui.isMobile"
      @click="ui.toggleSidebar()"
      class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
    >
      <IconMenu2 :size="20" />
    </button>

    <!-- Chat title -->
    <div class="min-w-0 flex-1">
      <h1 v-if="chat.activeChat" class="truncate text-sm font-medium text-text-primary">
        {{ chat.activeChat.name }}
      </h1>
      <span v-else class="text-sm text-text-muted">Select or create a chat</span>
    </div>

    <!-- Model selector -->
    <ModelSelector v-if="chat.activeChat" />

    <!-- Model manager button -->
    <button
      @click="ui.modelManagerOpen = true"
      class="rounded-md p-2 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
      title="Model Manager"
    >
      <IconCpu :size="18" />
    </button>
  </header>
</template>

<script setup lang="ts">
import { IconMessagePlus } from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'

const chat = useChatStore()
</script>

<template>
  <div class="flex h-full flex-col items-center justify-center px-4">
    <div class="mb-6 rounded-2xl bg-surface-2 p-5">
      <img src="/logo.png" alt="Ollama GUI" class="h-16 w-16" />
    </div>
    <h2 class="mb-2 text-xl font-semibold text-text-primary">Ollama GUI</h2>
    <p class="mb-6 max-w-sm text-center text-sm text-text-secondary">
      Chat with your local LLMs. Select an existing conversation or start a new
      one.
    </p>
    <button
      @click="chat.createChat()"
      class="flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-soft"
    >
      <IconMessagePlus :size="18" />
      New Chat
    </button>
  </div>
</template>

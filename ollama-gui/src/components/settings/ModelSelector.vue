<script setup lang="ts">
import { ref } from 'vue'
import { IconRefresh } from '@tabler/icons-vue'
import { useModelStore } from '@/stores/modelStore'
import { useChatStore } from '@/stores/chatStore'

const models = useModelStore()
const chat = useChatStore()
const refreshing = ref(false)

async function handleRefresh() {
  refreshing.value = true
  await Promise.all([models.refresh(), new Promise((r) => setTimeout(r, 500))])
  refreshing.value = false
}

function handleChange(e: Event) {
  const value = (e.target as HTMLSelectElement).value
  if (chat.activeChat?.id) {
    chat.switchModel(chat.activeChat.id, value)
  }
}
</script>

<template>
  <div class="flex items-center gap-1">
    <select
      :value="chat.activeChat?.model || models.currentModel"
      @change="handleChange"
      class="max-w-[280px] cursor-pointer rounded-lg border border-[var(--color-border)] bg-surface-2 py-1.5 pl-2 pr-7 text-xs text-text-primary focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
    >
      <option value="" disabled>Select a model</option>
      <option v-for="model in models.rankedModels" :key="model.name" :value="model.name">
        {{ model.displayName }}
      </option>
    </select>
    <button
      @click="handleRefresh"
      :disabled="refreshing"
      class="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary disabled:opacity-50"
      title="Refresh models"
    >
      <IconRefresh :size="16" :class="{ 'animate-spin': refreshing }" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { IconX } from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'

const chat = useChatStore()
const ui = useUiStore()

const currentPrompt = ref('')

const activeModel = computed(() => chat.activeChat?.model || '')

watch(
  activeModel,
  (model) => {
    currentPrompt.value = chat.systemPrompts.get(model) || ''
  },
  { immediate: true },
)

function save() {
  if (activeModel.value) {
    chat.setSystemPrompt(activeModel.value, currentPrompt.value)
  }
}
</script>

<template>
  <div class="flex h-full flex-col border-l border-[var(--color-border)] bg-surface-1">
    <div class="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
      <h2 class="text-sm font-semibold text-text-primary">System Prompt</h2>
      <button
        @click="ui.systemPromptOpen = false"
        class="rounded-md p-1 text-text-muted hover:bg-surface-2 hover:text-text-primary"
      >
        <IconX :size="18" />
      </button>
    </div>

    <div class="flex-1 p-4">
      <p class="mb-2 text-xs text-text-muted">
        {{ activeModel ? `Custom prompt for ${activeModel}` : 'Select a model first' }}
      </p>
      <textarea
        v-model="currentPrompt"
        @blur="save"
        :disabled="!activeModel"
        placeholder="Enter a system prompt..."
        rows="8"
        class="w-full resize-none rounded-lg border border-[var(--color-border)] bg-surface-2 p-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
      />

      <div class="mt-4">
        <h3 class="mb-2 text-xs font-medium text-text-muted">Default System Prompt</h3>
        <textarea
          v-model="chat.defaultSystemPrompt"
          placeholder="Default prompt for all models..."
          rows="4"
          class="w-full resize-none rounded-lg border border-[var(--color-border)] bg-surface-2 p-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>
    </div>
  </div>
</template>

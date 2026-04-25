<script setup lang="ts">
import type { Message } from '@/types/chat'
import { IconInfoCircle } from '@tabler/icons-vue'

defineProps<{
  message: Message
}>()
</script>

<template>
  <div class="flex justify-center px-4">
    <div
      class="flex max-w-md items-start gap-2 rounded-lg border border-[var(--color-border)] bg-surface-2 px-3 py-2 text-xs text-text-secondary"
    >
      <IconInfoCircle :size="14" class="mt-0.5 flex-shrink-0 text-accent" />
      <span class="whitespace-pre-wrap">{{ message.content }}</span>
    </div>
  </div>
</template>

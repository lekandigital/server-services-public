<script setup lang="ts">
import { ref } from 'vue'
import { formatDistanceToNow } from 'date-fns'
import type { Message } from '@/types/chat'

defineProps<{
  message: Message
}>()

const showTime = ref(false)
</script>

<template>
  <div class="flex justify-end" @mouseenter="showTime = true" @mouseleave="showTime = false">
    <div class="max-w-[80%] space-y-1">
      <div
        class="rounded-2xl rounded-br-md bg-[var(--color-user-bubble)] px-4 py-2.5 text-sm text-[var(--color-user-bubble-text)]"
      >
        <!-- Images -->
        <div v-if="message.images?.length" class="mb-2 flex flex-wrap gap-2">
          <img
            v-for="(img, i) in message.images"
            :key="i"
            :src="`data:image/png;base64,${img}`"
            class="max-h-48 rounded-lg"
            alt="Attached image"
          />
        </div>
        <div class="whitespace-pre-wrap break-words">{{ message.content }}</div>
      </div>
      <Transition name="fade">
        <div v-if="showTime" class="text-right text-2xs text-text-muted">
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
</style>

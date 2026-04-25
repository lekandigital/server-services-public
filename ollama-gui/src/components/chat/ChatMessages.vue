<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { IconArrowDown } from '@tabler/icons-vue'
import MessageBubble from './MessageBubble.vue'
import StreamingMessage from './StreamingMessage.vue'

const chat = useChatStore()
const settings = useSettingsStore()

const container = ref<HTMLElement>()
const isAtBottom = ref(true)
const userScrolledUp = ref(false)

function scrollToBottom(smooth = false) {
  if (!container.value) return
  container.value.scrollTo({
    top: container.value.scrollHeight,
    behavior: smooth ? 'smooth' : 'instant',
  })
  userScrolledUp.value = false
}

function handleScroll() {
  if (!container.value) return
  const { scrollTop, scrollHeight, clientHeight } = container.value
  const threshold = 100
  isAtBottom.value = scrollHeight - scrollTop - clientHeight < threshold

  // If user scrolls up during streaming, don't auto-scroll
  if (chat.isStreaming && !isAtBottom.value) {
    userScrolledUp.value = true
  }
  if (isAtBottom.value) {
    userScrolledUp.value = false
  }
}

// Auto-scroll when new messages arrive
watch(
  () => chat.messages.length,
  () => {
    if (!userScrolledUp.value) {
      nextTick(() => scrollToBottom())
    }
  },
)

// Auto-scroll during streaming (if user hasn't scrolled up)
watch(
  () => chat.streamingContent,
  () => {
    if (!userScrolledUp.value) {
      nextTick(() => scrollToBottom())
    }
  },
)

// Scroll to bottom when chat changes
watch(
  () => chat.activeChatId,
  () => {
    userScrolledUp.value = false
    nextTick(() => scrollToBottom())
  },
)

onMounted(() => scrollToBottom())

const visibleMessages = () => {
  if (settings.showSystemMessages) return chat.messages
  return chat.messages.filter((m) => m.role !== 'system')
}
</script>

<template>
  <div
    ref="container"
    class="h-full overflow-y-auto px-4 py-6"
    @scroll="handleScroll"
  >
    <div class="mx-auto max-w-3xl space-y-4">
      <MessageBubble
        v-for="msg in visibleMessages()"
        :key="msg.id"
        :message="msg"
      />

      <!-- Streaming response -->
      <StreamingMessage
        v-if="chat.isStreaming && chat.streamingContent"
      />
    </div>

    <!-- Scroll-to-bottom pill -->
    <Transition name="fade">
      <button
        v-if="userScrolledUp && chat.isStreaming"
        @click="scrollToBottom(true)"
        class="fixed bottom-24 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white shadow-lg transition-colors hover:bg-accent-soft"
      >
        <IconArrowDown :size="14" />
        New messages
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-fast);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

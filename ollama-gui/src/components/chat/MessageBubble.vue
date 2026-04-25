<script setup lang="ts">
import type { Message } from '@/types/chat'
import UserMessage from './UserMessage.vue'
import AiMessage from './AiMessage.vue'
import SystemMessage from './SystemMessage.vue'

defineProps<{
  message: Message
}>()
</script>

<template>
  <UserMessage v-if="message.role === 'user'" :message="message" />
  <AiMessage v-else-if="message.role === 'assistant'" :message="message" />
  <SystemMessage v-else-if="message.role === 'system'" :message="message" />
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chatStore'
import ChatHeader from './ChatHeader.vue'
import ChatMessages from './ChatMessages.vue'
import ChatInput from './ChatInput.vue'
import EmptyState from './EmptyState.vue'

const chat = useChatStore()
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- Header bar -->
    <ChatHeader />

    <!-- Messages or empty state -->
    <div class="relative flex-1 overflow-hidden">
      <ChatMessages v-if="chat.activeChat" />
      <EmptyState v-else />
    </div>

    <!-- Input -->
    <ChatInput v-if="chat.activeChat" />
  </div>
</template>

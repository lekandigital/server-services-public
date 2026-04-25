<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import ChatListItem from './ChatListItem.vue'

const props = defineProps<{
  searchQuery: string
}>()

const chat = useChatStore()
const ui = useUiStore()

const filteredChats = computed(() => {
  if (!props.searchQuery) return chat.sortedChats
  const q = props.searchQuery.toLowerCase()
  return chat.sortedChats.filter((c) => c.name.toLowerCase().includes(q))
})

function handleSelect(chatId: number) {
  chat.selectChat(chatId)
  if (ui.isMobile) ui.closeSidebar()
}
</script>

<template>
  <div class="p-2">
    <div v-if="filteredChats.length === 0" class="px-3 py-8 text-center text-sm text-text-muted">
      {{ searchQuery ? 'No matching chats' : 'No chats yet. Start a new one!' }}
    </div>
    <ChatListItem
      v-for="c in filteredChats"
      :key="c.id"
      :chat="c"
      :active="c.id === chat.activeChatId"
      @select="handleSelect(c.id!)"
      @delete="chat.deleteChat(c.id!)"
      @rename="(name) => chat.renameChat(c.id!, name)"
      @pin="chat.togglePin(c.id!)"
      @duplicate="chat.duplicateChat(c.id!)"
    />
  </div>
</template>

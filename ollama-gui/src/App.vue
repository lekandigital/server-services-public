<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '@/stores/settingsStore'
import { useConnectionStore } from '@/stores/connectionStore'
import { useModelStore } from '@/stores/modelStore'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useAutoTitle } from '@/composables/useAutoTitle'
import AppLayout from '@/components/ui/AppLayout.vue'

const settings = useSettingsStore()
const connection = useConnectionStore()
const models = useModelStore()
const chat = useChatStore()
const ui = useUiStore()

useKeyboardShortcuts()
useAutoTitle()

onMounted(async () => {
  settings.applyTheme()
  ui.initSidebar()
  chat.loadSystemPrompts()
  connection.startPolling()
  await Promise.all([chat.loadChats(), models.refresh()])
})
</script>

<template>
  <AppLayout />
</template>

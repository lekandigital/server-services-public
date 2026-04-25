<script setup lang="ts">
import { useUiStore } from '@/stores/uiStore'
import Sidebar from '@/components/sidebar/Sidebar.vue'
import ChatView from '@/components/chat/ChatView.vue'
import ConnectionStatus from '@/components/settings/ConnectionStatus.vue'
import SettingsPanel from '@/components/settings/SettingsPanel.vue'
import SystemPromptPanel from '@/components/settings/SystemPromptPanel.vue'
import ModelManager from '@/components/models/ModelManager.vue'
import CommandPalette from '@/components/ui/CommandPalette.vue'

const ui = useUiStore()
</script>

<template>
  <div class="flex h-full bg-surface-0">
    <!-- Mobile overlay -->
    <Transition name="fade">
      <div
        v-if="ui.isMobile && ui.sidebarOpen"
        class="fixed inset-0 z-30 bg-black/50"
        @click="ui.closeSidebar()"
      />
    </Transition>

    <!-- Sidebar -->
    <Transition name="slide-sidebar">
      <aside
        v-show="ui.sidebarOpen"
        :class="[
          'flex h-full flex-col border-r border-[var(--color-border)] bg-surface-1',
          ui.isMobile
            ? 'fixed inset-y-0 left-0 z-40 w-80'
            : 'relative w-80 flex-shrink-0',
        ]"
      >
        <Sidebar />
      </aside>
    </Transition>

    <!-- Main content -->
    <main class="relative flex min-w-0 flex-1 flex-col">
      <ChatView />
    </main>

    <!-- Right panel (settings or system prompt) -->
    <Transition name="slide-right">
      <aside
        v-if="ui.settingsOpen || ui.systemPromptOpen"
        :class="[
          'h-full flex-shrink-0',
          ui.isMobile
            ? 'fixed inset-y-0 right-0 z-40 w-80'
            : 'relative w-80',
        ]"
      >
        <SettingsPanel v-if="ui.settingsOpen" />
        <SystemPromptPanel v-else-if="ui.systemPromptOpen" />
      </aside>
    </Transition>

    <!-- Mobile overlay for right panel -->
    <Transition name="fade">
      <div
        v-if="ui.isMobile && (ui.settingsOpen || ui.systemPromptOpen)"
        class="fixed inset-0 z-30 bg-black/50"
        @click="ui.closeAllPanels()"
      />
    </Transition>

    <!-- Connection status indicator -->
    <ConnectionStatus />

    <!-- Modals -->
    <Teleport to="body">
      <ModelManager v-if="ui.modelManagerOpen" />
    </Teleport>
    <CommandPalette />
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-normal);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-sidebar-enter-active,
.slide-sidebar-leave-active {
  transition: transform var(--transition-slow);
}
.slide-sidebar-enter-from,
.slide-sidebar-leave-to {
  transform: translateX(-100%);
}

.slide-right-enter-active,
.slide-right-leave-active {
  transition: transform var(--transition-slow);
}
.slide-right-enter-from,
.slide-right-leave-to {
  transform: translateX(100%);
}
</style>

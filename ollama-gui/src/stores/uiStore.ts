import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useMediaQuery } from '@vueuse/core'

export const useUiStore = defineStore('ui', () => {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const isTablet = useMediaQuery('(min-width: 768px) and (max-width: 1023px)')
  const isDesktop = useMediaQuery('(min-width: 1024px)')

  const sidebarOpen = ref(false)
  const settingsOpen = ref(false)
  const systemPromptOpen = ref(false)
  const commandPaletteOpen = ref(false)
  const modelManagerOpen = ref(false)

  const sidebarWidth = ref(320)

  // On desktop, sidebar starts open
  // On mobile/tablet, starts closed
  function initSidebar() {
    sidebarOpen.value = isDesktop.value
  }

  function toggleSidebar() {
    sidebarOpen.value = !sidebarOpen.value
  }

  function closeSidebar() {
    sidebarOpen.value = false
  }

  function toggleSettings() {
    settingsOpen.value = !settingsOpen.value
    if (settingsOpen.value) systemPromptOpen.value = false
  }

  function toggleSystemPrompt() {
    systemPromptOpen.value = !systemPromptOpen.value
    if (systemPromptOpen.value) settingsOpen.value = false
  }

  function closeAllPanels() {
    settingsOpen.value = false
    systemPromptOpen.value = false
    commandPaletteOpen.value = false
    modelManagerOpen.value = false
  }

  return {
    isMobile,
    isTablet,
    isDesktop,
    sidebarOpen,
    settingsOpen,
    systemPromptOpen,
    commandPaletteOpen,
    modelManagerOpen,
    sidebarWidth,
    initSidebar,
    toggleSidebar,
    closeSidebar,
    toggleSettings,
    toggleSystemPrompt,
    closeAllPanels,
  }
})

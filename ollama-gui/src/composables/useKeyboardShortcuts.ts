import { onMounted, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useUiStore } from '@/stores/uiStore'

export function useKeyboardShortcuts() {
  const chat = useChatStore()
  const settings = useSettingsStore()
  const ui = useUiStore()

  function handleKeydown(e: KeyboardEvent) {
    const meta = e.metaKey || e.ctrlKey

    // Escape — close any panel
    if (e.key === 'Escape') {
      if (ui.commandPaletteOpen) {
        ui.commandPaletteOpen = false
      } else if (ui.settingsOpen || ui.systemPromptOpen) {
        ui.closeAllPanels()
      } else if (ui.isMobile && ui.sidebarOpen) {
        ui.closeSidebar()
      }
      return
    }

    // Cmd+N — new chat
    if (meta && e.key === 'n') {
      e.preventDefault()
      chat.createChat()
      return
    }

    // Cmd+K — command palette / search
    if (meta && e.key === 'k') {
      e.preventDefault()
      ui.commandPaletteOpen = !ui.commandPaletteOpen
      return
    }

    // Cmd+Shift+S — settings
    if (meta && e.shiftKey && e.key === 'S') {
      e.preventDefault()
      ui.toggleSettings()
      return
    }

    // Cmd+D — toggle dark mode
    if (meta && e.key === 'd') {
      e.preventDefault()
      settings.toggleTheme()
      return
    }

    // Cmd+Backspace — delete current chat
    if (meta && e.key === 'Backspace' && chat.activeChatId) {
      e.preventDefault()
      chat.deleteChat(chat.activeChatId)
      return
    }

    // Cmd+1-9 — switch to chat by position
    if (meta && e.key >= '1' && e.key <= '9') {
      const index = parseInt(e.key) - 1
      const sorted = chat.sortedChats
      if (index < sorted.length && sorted[index].id) {
        e.preventDefault()
        chat.selectChat(sorted[index].id!)
      }
      return
    }
  }

  onMounted(() => {
    window.addEventListener('keydown', handleKeydown)
  })

  onUnmounted(() => {
    window.removeEventListener('keydown', handleKeydown)
  })
}

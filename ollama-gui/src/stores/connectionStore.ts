import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ConnectionStatus } from '@/types/ollama'
import { checkConnection } from '@/services/ollama'

export const useConnectionStore = defineStore('connection', () => {
  const status = ref<ConnectionStatus>('connecting')
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function check() {
    const wasConnected = status.value === 'connected'
    status.value = 'connecting'

    const connected = await checkConnection()
    status.value = connected ? 'connected' : 'disconnected'

    if (!wasConnected && connected) {
      console.info('[ollama] Connection established')
    } else if (wasConnected && !connected) {
      console.warn('[ollama] Connection lost')
    }

    return connected
  }

  function startPolling(intervalMs = 10000) {
    stopPolling()
    check()
    pollTimer = setInterval(check, intervalMs)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    status,
    check,
    startPolling,
    stopPolling,
  }
})

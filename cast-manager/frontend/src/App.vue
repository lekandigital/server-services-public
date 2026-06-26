<script setup lang="ts">
import { onMounted } from 'vue'
import AppShell from './components/shell/AppShell.vue'
import { useLibraryStore } from './stores/libraryStore'
import { useCastStore } from './stores/castStore'
import { useAppStore } from './stores/appStore'

const library = useLibraryStore()
const cast = useCastStore()
const app = useAppStore()

onMounted(async () => {
  window.addEventListener('cast-manager-api-error', ((event: CustomEvent) => {
    if (event.detail) app.recordApiError(event.detail)
  }) as EventListener)
  await library.init()
  app.syncFromLocation()
  window.addEventListener('popstate', async () => {
    app.syncFromLocation()
    library.previewOpen = false
    library.castPanelOpen = false
    if (app.section === 'library') await library.loadFromRoute()
  })
  cast.startPolling()
  cast.refreshDevices()
  cast.loadDoctor()
})
</script>

<template>
  <AppShell />
</template>

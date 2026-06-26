<script setup lang="ts">
import { computed, ref } from 'vue'
import SidebarNav from './SidebarNav.vue'
import TopBar from './TopBar.vue'
import ToastHost from '../common/ToastHost.vue'
import UploadOverlay from '../common/UploadOverlay.vue'
import DiagnosticsDrawer from '../diagnostics/DiagnosticsDrawer.vue'
import NowPlayingBar from '../cast/NowPlayingBar.vue'
import DashboardView from '../dashboard/DashboardView.vue'
import LibraryView from '../library/LibraryView.vue'
import DriveView from '../drive/DriveView.vue'
import TorrentView from '../torrents/TorrentView.vue'
import StorageView from '../storage/StorageView.vue'
import ActivityView from '../activity/ActivityView.vue'
import SettingsView from '../settings/SettingsView.vue'
import DiagnosticsView from '../diagnostics/DiagnosticsView.vue'
import RecentView from '../activity/RecentView.vue'
import StarredView from '../activity/StarredView.vue'
import SharedView from '../activity/SharedView.vue'
import TrashView from '../activity/TrashView.vue'
import QueueView from '../activity/QueueView.vue'
import PlaylistsView from '../activity/PlaylistsView.vue'
import MediaPreviewPanel from '../preview/MediaPreviewPanel.vue'
import CastPanel from '../cast/CastPanel.vue'
import { useAppStore } from '../../stores/appStore'
import { useCastStore } from '../../stores/castStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { uploadDriveFile, fetchDriveConfig } from '../../api/drive'

const app = useAppStore()
const cast = useCastStore()
const library = useLibraryStore()
const globalDragActive = ref(false)
const driveUploadPath = ref('/home/REDACTED_USER/file-manager/drive')
let dragCounter = 0

// Resolve the actual Drive path from config
fetchDriveConfig().then((cfg) => {
  if (cfg.library_path) driveUploadPath.value = cfg.library_path
}).catch(() => {})

const shellClass = computed(() => ({
  'app-shell': true,
  'sidebar-collapsed': app.sidebarCollapsed,
}))

function onGlobalDragEnter(event: DragEvent) {
  event.preventDefault()
  dragCounter += 1
  if (event.dataTransfer?.types.includes('Files')) {
    globalDragActive.value = true
  }
}

function onGlobalDragLeave(event: DragEvent) {
  event.preventDefault()
  dragCounter -= 1
  if (dragCounter <= 0) {
    dragCounter = 0
    globalDragActive.value = false
  }
}

function onGlobalDragOver(event: DragEvent) {
  event.preventDefault()
}

async function onGlobalDrop(event: DragEvent) {
  event.preventDefault()
  dragCounter = 0
  globalDragActive.value = false
  const files = event.dataTransfer?.files
  if (!files?.length) return
  const target = driveUploadPath.value
  const list = Array.from(files)
  app.toast(`Uploading ${list.length} file${list.length === 1 ? '' : 's'} to Drive`, 'info')
  for (const file of list) {
    try {
      await uploadDriveFile(target, file, () => {})
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Upload failed'
      app.toast(`${file.name}: ${msg}`, 'error')
    }
  }
  app.toast('Uploaded to Drive', 'success')
  // Refresh if user is viewing the drive folder
  if (!library.showRootView && library.currentPath === target) {
    await library.load(undefined, { updateHistory: false })
  }
}
</script>

<template>
  <div
    :class="shellClass"
    @dragenter="onGlobalDragEnter"
    @dragleave="onGlobalDragLeave"
    @dragover="onGlobalDragOver"
    @drop="onGlobalDrop"
  >
    <UploadOverlay :visible="globalDragActive" target-label="Drop to upload to Drive" :target-path="driveUploadPath" />
    <SidebarNav />
    <div class="app-main">
      <TopBar />
      <div class="app-content">
        <DashboardView v-if="app.section === 'dashboard'" />
        <DriveView v-else-if="app.section === 'drive'" />
        <LibraryView v-else-if="app.section === 'library'" />
        <RecentView v-else-if="app.section === 'recent'" />
        <StarredView v-else-if="app.section === 'starred'" />
        <SharedView v-else-if="app.section === 'shared'" />
        <TorrentView v-else-if="app.section === 'torrents'" />
        <QueueView v-else-if="app.section === 'queue'" />
        <PlaylistsView v-else-if="app.section === 'playlists'" />
        <StorageView v-else-if="app.section === 'storage'" />
        <TrashView v-else-if="app.section === 'trash'" />
        <ActivityView v-else-if="app.section === 'activity'" />
        <SettingsView v-else-if="app.section === 'settings'" />
        <DiagnosticsView v-else-if="app.section === 'diagnostics'" />
      </div>
      <NowPlayingBar v-if="cast.showNowPlaying" />
    </div>
    <ToastHost />
    <DiagnosticsDrawer v-if="app.diagnosticsOpen" />
    <MediaPreviewPanel v-if="library.previewOpen" />
    <CastPanel v-if="library.castPanelOpen" />
  </div>
</template>

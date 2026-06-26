<script setup lang="ts">
import { computed } from 'vue'
import { useAppStore } from '../../stores/appStore'
import { useCastStore } from '../../stores/castStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { useTorrentStore } from '../../stores/torrentStore'
import IconGlyph from '../common/IconGlyph.vue'

const app = useAppStore()
const cast = useCastStore()
const library = useLibraryStore()
const torrents = useTorrentStore()

const titles: Record<string, { title: string; eyebrow: string }> = {
  dashboard: { title: 'Good evening', eyebrow: 'Your media at a glance' },
  drive: { title: 'Drive / Files', eyebrow: 'Browse and manage server storage' },
  library: { title: 'Media Library', eyebrow: library.mediaRoot },
  recent: { title: 'Recently opened', eyebrow: 'Pick up where you left off' },
  starred: { title: 'Starred', eyebrow: 'Your saved files and folders' },
  shared: { title: 'Shared links', eyebrow: 'Manage access to your media' },
  torrents: { title: 'Torrents', eyebrow: 'Downloads and transfers' },
  queue: { title: 'Play queue', eyebrow: 'This browser’s upcoming media' },
  playlists: { title: 'Playlists', eyebrow: 'Collections saved in this browser' },
  storage: { title: 'Storage', eyebrow: 'Capacity and media usage' },
  trash: { title: 'Trash', eyebrow: 'Restorable items' },
  activity: { title: 'Activity', eyebrow: 'Recent server operations' },
  settings: { title: 'Settings', eyebrow: 'Playback and interface defaults' },
  diagnostics: { title: 'Diagnostics', eyebrow: 'Health, failures, and cast details' },
}
const heading = computed(() => app.section === 'library'
  ? { title: 'Files', eyebrow: library.currentPath }
  : (titles[app.section] || titles.dashboard))

async function refresh() {
  if (app.section === 'library') await library.load()
  else if (app.section === 'torrents') await torrents.load()
  else await Promise.allSettled([cast.pollStatus(), cast.refreshDevices()])
  app.toast('View refreshed', 'success', 'low')
}
</script>

<template>
  <header class="topbar">
    <button class="icon-button mobile-menu" aria-label="Open navigation" @click="app.mobileNavOpen = !app.mobileNavOpen"><IconGlyph name="menu" /></button>
    <button class="icon-button desktop-menu" :aria-label="app.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="app.toggleSidebar()"><IconGlyph name="menu" /></button>
    <div class="topbar-heading">
      <span>{{ heading.eyebrow }}</span>
      <strong>{{ heading.title }}</strong>
    </div>
    <div style="flex:1" />
    <button class="icon-button" aria-label="Refresh current view" title="Refresh current view" @click="refresh"><IconGlyph name="refresh" /></button>
    <div class="topbar-status">
      <span class="status-dot" :class="cast.uiState === 'error' ? 'offline' : 'online'" />
      <div><strong>{{ cast.selectedDevice?.name || 'Cast ready' }}</strong><span>{{ cast.uiState.replaceAll('_', ' ') }}</span></div>
    </div>
    <button class="btn btn-secondary" @click="app.setSection('diagnostics')"><IconGlyph name="diagnostics" :size="16" /> Diagnostics</button>
  </header>
</template>

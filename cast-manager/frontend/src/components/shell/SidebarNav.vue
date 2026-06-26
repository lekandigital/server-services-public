<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAppStore } from '../../stores/appStore'
import { useCastStore } from '../../stores/castStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { useActivityStore } from '../../stores/activityStore'
import { getStorageStats } from '../../api/storage'
import IconGlyph from '../common/IconGlyph.vue'
import type { FileRoot, NavSection } from '../../types'

const app = useAppStore()
const cast = useCastStore()
const library = useLibraryStore()
const activity = useActivityStore()
const storagePercent = ref<number | null>(null)

const pinnedRoots = computed(() => {
  const pinIds = ['drive', 'downloads', 'Downloads', 'watch_list']
  return library.fileRoots.filter((root) => pinIds.includes(root.id))
})

const browseItems: Array<{ id: NavSection; label: string; icon: string; count?: () => number }> = [
  { id: 'recent', label: 'Recent', icon: 'recent', count: () => activity.recent.length },
  { id: 'starred', label: 'Starred', icon: 'star', count: () => activity.starred.length },
  { id: 'shared', label: 'Shared', icon: 'shared' },
  { id: 'trash', label: 'Trash', icon: 'trash' },
]
const manageItems: Array<{ id: NavSection; label: string; icon: string }> = [
  { id: 'torrents', label: 'Torrents', icon: 'torrents' },
  { id: 'queue', label: 'Queue', icon: 'queue' },
  { id: 'playlists', label: 'Playlists', icon: 'playlists' },
  { id: 'storage', label: 'Storage', icon: 'storage' },
  { id: 'activity', label: 'Activity', icon: 'activity' },
]

function activeRoot(root?: FileRoot) {
  return !!root && app.section === 'library' && !library.showRootView && library.browseMode && (library.currentPath === root.serverPath || library.currentPath.startsWith(`${root.serverPath}/`))
}

const activeBrowse = computed(() => app.section === 'library' && library.browseMode && !pinnedRoots.value.some((root) => activeRoot(root)))

async function openRoot(root?: FileRoot) {
  if (root) await library.navigateToBrowse(root.serverPath)
}

onMounted(async () => {
  if (!library.config) await library.init()
  await Promise.allSettled([activity.loadStarred(), activity.loadRecent()])
  try {
    const stats = await getStorageStats() as { totalSpace?: number; usedSpace?: number }
    if (stats.totalSpace) storagePercent.value = Math.round(((stats.usedSpace || 0) / stats.totalSpace) * 100)
  } catch { storagePercent.value = null }
})
</script>

<template>
  <aside class="sidebar" :class="{ open: app.mobileNavOpen }">
    <div class="sidebar-brand">
      <div class="brand-mark"><IconGlyph name="cast" :size="20" /></div>
      <div v-if="!app.sidebarCollapsed"><strong>File Manager</strong><span>Your files and media</span></div>
    </div>
    <nav class="sidebar-nav" aria-label="Primary navigation">
      <div v-if="!app.sidebarCollapsed" class="nav-group">Browse</div>
      <button class="nav-item" :class="{ active: app.section === 'dashboard' }" @click="app.setSection('dashboard')"><IconGlyph name="dashboard" :size="18" /><span v-if="!app.sidebarCollapsed">Dashboard</span></button>
      <button class="nav-item" :class="{ active: app.section === 'library' && library.showRootView }" @click="library.goToRoot()"><IconGlyph name="library" :size="18" /><span v-if="!app.sidebarCollapsed">File Manager</span></button>
      <button v-for="root in pinnedRoots" :key="root.id" class="nav-item nav-subitem" :class="{ active: activeRoot(root) }" @click="openRoot(root)"><IconGlyph :name="root.id === 'drive' ? 'library' : root.id === 'watch_list' ? 'cast' : 'torrents'" :size="18" /><span v-if="!app.sidebarCollapsed">{{ root.label }}</span></button>
      <button class="nav-item nav-subitem" :class="{ active: activeBrowse }" @click="library.navigateToBrowse('/')"><IconGlyph name="storage" :size="18" /><span v-if="!app.sidebarCollapsed">Browse Server</span></button>
      <button v-for="item in browseItems" :key="item.id" class="nav-item" :class="{ active: app.section === item.id }" :aria-label="item.label" @click="app.setSection(item.id)"><IconGlyph :name="item.icon" :size="18" /><span v-if="!app.sidebarCollapsed">{{ item.label }}</span><span v-if="!app.sidebarCollapsed && item.count?.()" class="nav-count">{{ item.count() }}</span></button>

      <div v-if="!app.sidebarCollapsed" class="nav-group">Manage</div>
      <button v-for="item in manageItems" :key="item.id" class="nav-item" :class="{ active: app.section === item.id }" @click="app.setSection(item.id)"><IconGlyph :name="item.icon" :size="18" /><span v-if="!app.sidebarCollapsed">{{ item.label }}</span></button>
      <div v-if="!app.sidebarCollapsed && storagePercent !== null" class="sidebar-storage"><div><span>Storage</span><strong>{{ storagePercent }}% used</strong></div><div class="progress-track"><div class="progress-bar" :style="{ width: `${storagePercent}%` }" /></div></div>

      <div v-if="!app.sidebarCollapsed" class="nav-group">System</div>
      <button class="nav-item" :class="{ active: app.section === 'diagnostics' }" @click="app.setSection('diagnostics')"><IconGlyph name="diagnostics" :size="18" /><span v-if="!app.sidebarCollapsed">Diagnostics</span></button>
      <button class="nav-item" :class="{ active: app.section === 'settings' }" @click="app.setSection('settings')"><IconGlyph name="settings" :size="18" /><span v-if="!app.sidebarCollapsed">Settings</span></button>
    </nav>
    <div class="sidebar-status" v-if="!app.sidebarCollapsed">
      <span class="status-dot" :class="cast.uiState === 'error' ? 'offline' : 'online'" />
      <div><strong>{{ cast.showNowPlaying ? 'Casting now' : (app.serverOnline ? 'Cast ready' : 'Server unavailable') }}</strong><span>{{ cast.selectedDevice?.name || 'No cast device selected' }} · {{ cast.uiState.replaceAll('_', ' ') }}</span></div>
    </div>
  </aside>
</template>

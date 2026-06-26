<script setup lang="ts">
import { useAppStore } from '../../stores/appStore'
import type { NavSection } from '../../types'

const app = useAppStore()

const SECTION_LABELS: Record<NavSection, string> = {
  dashboard: 'Dashboard',
  drive: 'Drive',
  library: 'Files',
  recent: 'Recent',
  starred: 'Starred',
  shared: 'Shared',
  torrents: 'Torrents',
  queue: 'Queue',
  playlists: 'Playlists',
  storage: 'Storage',
  trash: 'Trash',
  activity: 'Activity',
  settings: 'Settings',
  diagnostics: 'Diagnostics',
}

function label(section: NavSection): string {
  return SECTION_LABELS[section] || section
}
</script>

<template>
  <nav class="breadcrumbs" aria-label="Section breadcrumbs">
    <button @click="app.setSection('dashboard')">File Manager</button>
    <span class="breadcrumb-sep">/</span>
    <span>{{ label(app.section) }}</span>
  </nav>
</template>

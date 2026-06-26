<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ContinueWatching from './ContinueWatching.vue'
import QuickCastCard from './QuickCastCard.vue'
import StorageSummary from './StorageSummary.vue'
import { useTorrentStore } from '../../stores/torrentStore'
import { useCastStore } from '../../stores/castStore'
import { useAppStore } from '../../stores/appStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { formatBytes, formatDuration } from '../../utils/files'
import { getDiskStats } from '../../api/storage'
import IconGlyph from '../common/IconGlyph.vue'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'

const torrents = useTorrentStore()
const cast = useCastStore()
const app = useAppStore()
const library = useLibraryStore()
const diskFree = ref('—')
const diskError = ref('')

const castHealth = computed(() => cast.uiState === 'error' ? 'Needs attention' : (cast.selectedDevice ? 'Ready to cast' : 'Choose a device'))

onMounted(async () => {
  await torrents.load()
  try {
    const disk = await getDiskStats()
    diskFree.value = formatBytes((disk as { free?: number }).free)
  } catch (err) { diskError.value = err instanceof Error ? err.message : 'Disk status unavailable' }
})

function openLibrary() {
  library.goToRoot()
}
</script>

<template>
  <section class="page-stack">
    <div class="page-actions">
      <div><span class="eyebrow">Media command center</span><h1 class="page-title">Everything ready when you are.</h1><SectionBreadcrumbs /><p class="page-description">Browse the library, send a URL, or check the active cast without digging through system details.</p></div>
      <button class="btn btn-primary" @click="openLibrary"><IconGlyph name="library" :size="16" /> Open Files</button>
    </div>

    <div class="dashboard-grid">
      <div class="dashboard-main">
        <article class="card" data-testid="active-cast-card">
          <div class="card-header" style="margin:-20px -20px 18px">
            <div><span class="eyebrow">Living room control</span><h2>{{ cast.showNowPlaying ? 'Active cast' : 'Nothing playing' }}</h2></div>
            <span class="status-badge" :class="cast.showNowPlaying ? 'success' : 'neutral'">{{ cast.uiState.replaceAll('_', ' ') }}</span>
          </div>
          <template v-if="cast.showNowPlaying">
            <h3 style="font-size:18px;margin-bottom:6px">{{ cast.status?.title || cast.status?.session?.title || 'Cast session' }}</h3>
            <p style="color:var(--text-muted)">{{ cast.status?.deviceName || cast.selectedDevice?.name }} · {{ cast.status?.backend || 'automatic backend' }} · {{ formatDuration(cast.currentTime) }} / {{ formatDuration(cast.duration) }}</p>
            <div class="button-row"><button class="btn btn-secondary" @click="app.setSection('diagnostics')">View diagnostics</button></div>
          </template>
          <div v-else class="friendly-empty compact"><div class="empty-icon"><IconGlyph name="cast" :size="18" /></div><strong>Ready for your next cast</strong><p>Select a video from the Library or analyze a URL below. Cast controls will stay visible during startup, buffering, and seeking.</p></div>
        </article>
        <QuickCastCard />
        <ContinueWatching />
      </div>

      <aside class="dashboard-side">
        <div class="health-grid" style="grid-template-columns:1fr 1fr">
          <article class="health-card"><span class="status-dot" :class="cast.uiState === 'error' ? 'offline' : 'online'" /><div><span>Cast health</span><strong>{{ castHealth }}</strong></div></article>
          <article class="health-card"><span class="status-dot online" /><div><span>Media root</span><strong>watch_list</strong></div></article>
          <article class="health-card"><span class="status-dot" :class="torrents.error ? 'warning' : 'online'" /><div><span>Active torrents</span><strong>{{ torrents.stats.active }}</strong></div></article>
          <article class="health-card"><span class="status-dot" :class="diskError ? 'warning' : 'online'" /><div><span>Free storage</span><strong>{{ diskFree }}</strong></div></article>
        </div>
        <StorageSummary />
        <article class="card">
          <span class="eyebrow">Shortcuts</span><h2 class="card-title">Keep moving</h2>
          <div style="display:grid;gap:8px">
            <button class="btn btn-secondary" style="justify-content:flex-start" @click="app.setSection('torrents')"><IconGlyph name="torrents" :size="16" /> Manage torrents <span style="margin-left:auto;color:var(--text-subtle)">{{ torrents.stats.active }} active</span></button>
            <button class="btn btn-secondary" style="justify-content:flex-start" @click="app.setSection('storage')"><IconGlyph name="storage" :size="16" /> Review storage</button>
            <button class="btn btn-secondary" style="justify-content:flex-start" @click="app.setSection('diagnostics')"><IconGlyph name="diagnostics" :size="16" /> Open diagnostics</button>
          </div>
        </article>
      </aside>
    </div>
  </section>
</template>

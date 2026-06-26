<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getStorageStats, getStorageDirs } from '../../api/storage'
import { formatBytes } from '../../utils/files'
import { useLibraryStore } from '../../stores/libraryStore'
import { useAppStore } from '../../stores/appStore'
import IconGlyph from '../common/IconGlyph.vue'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'

const library = useLibraryStore()
const app = useAppStore()
const stats = ref<Record<string, any> | null>(null)
const dirs = ref<Array<{name:string;path:string;size:number;itemCount?:number}>>([])
const error = ref('')
const loading = ref(false)
const usedPercent = computed(() => stats.value?.totalSpace ? Math.round((stats.value.usedSpace / stats.value.totalSpace) * 100) : 0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    stats.value = await getStorageStats() as Record<string, any>
    const data = await getStorageDirs(library.mediaRoot)
    dirs.value = (data as any).dirs || []
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load storage'
  } finally { loading.value = false }
}

function openDir(path: string) { app.setSection('library'); library.load(path) }
onMounted(load)
</script>

<template>
  <section class="page-stack" data-testid="storage-page">
    <div class="page-actions">
      <div><span class="eyebrow">Capacity</span><h1 class="page-title">Storage</h1><SectionBreadcrumbs /><p class="page-description">Media root: <span class="mono">{{ library.mediaRoot }}</span></p></div>
      <button class="btn btn-secondary" :disabled="loading" :title="loading ? 'Storage check is already in progress' : undefined" @click="load"><IconGlyph name="refresh" :size="16" /> {{ loading ? 'Checking…' : 'Refresh' }}</button>
    </div>
    <div v-if="error" class="inline-message error-message"><div><strong>Storage metrics could not load</strong><p>{{ error }} · Expected GET /api/storage/stats and /api/storage/dirs</p></div><button class="btn btn-secondary" @click="load">Retry</button></div>
    <div v-else-if="loading && !stats" class="loading-state">Calculating storage usage…</div>
    <template v-else-if="stats">
      <div class="stats-row">
        <div class="stat-card"><div class="stat-value">{{ formatBytes(stats.usedSpace) }}</div><div class="stat-label">Used · {{ usedPercent }}%</div></div>
        <div class="stat-card"><div class="stat-value">{{ formatBytes(stats.freeSpace) }}</div><div class="stat-label">Free space</div></div>
        <div class="stat-card"><div class="stat-value">{{ formatBytes(stats.totalSpace) }}</div><div class="stat-label">Total capacity</div></div>
      </div>
      <article class="card panel-card">
        <div class="card-header"><div><span class="eyebrow">Largest media</span><h2>Largest files</h2></div><span class="status-badge neutral">{{ (stats.largestFiles || []).length }} files</span></div>
        <div v-if="!stats.largestFiles?.length" class="friendly-empty compact"><strong>No size data returned</strong><p>The disk summary is still available above.</p></div>
        <div v-for="file in (stats.largestFiles || [])" :key="file.path" class="diagnostic-row"><span class="method-badge">{{ formatBytes(file.size) }}</span><div><strong>{{ file.path.split('/').pop() }}</strong><span>{{ file.path }}</span></div></div>
      </article>
      <article class="card panel-card">
        <div class="card-header"><div><span class="eyebrow">Media directories</span><h2>Folder usage</h2></div></div>
        <div v-if="!dirs.length" class="friendly-empty compact"><strong>No directory breakdown available</strong><p>You can still browse the configured media root in Library.</p><button class="btn btn-secondary" style="margin-top:12px" @click="openDir(library.mediaRoot)">Open media root</button></div>
        <div v-for="dir in dirs" :key="dir.path" class="diagnostic-row"><span class="kind-mark">DIR</span><div><strong>{{ dir.name }}</strong><span>{{ formatBytes(dir.size) }} · {{ dir.itemCount || 0 }} items</span></div><button class="btn btn-secondary btn-sm" @click="openDir(dir.path)">Open</button></div>
      </article>
    </template>
  </section>
</template>

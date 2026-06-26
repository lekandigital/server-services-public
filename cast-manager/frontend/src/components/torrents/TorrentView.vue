<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AddTorrentPanel from './AddTorrentPanel.vue'
import { useTorrentStore } from '../../stores/torrentStore'
import { torrentInfo } from '../../api/torrents'
import { useAppStore } from '../../stores/appStore'
import { formatBytes } from '../../utils/files'
import IconGlyph from '../common/IconGlyph.vue'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'
import type { TorrentItem } from '../../types'

const torrents = useTorrentStore(); const app = useAppStore(); const selectedInfo = ref<unknown>(null); const selectedName = ref('')
onMounted(() => torrents.load())
async function info(t: TorrentItem) { try { selectedInfo.value = await torrentInfo(t.id); selectedName.value = t.name } catch (err) { app.toast(err instanceof Error ? err.message : 'Torrent info unavailable', 'error') } }
async function remove(t: TorrentItem, data = false) { const message = data ? `Remove “${t.name}” and permanently delete its downloaded data?` : `Remove “${t.name}” from the torrent client?`; if (!confirm(message)) return; await torrents.remove(t.id, data) }
</script>

<template><section class="page-stack" data-testid="torrents-page">
  <div class="page-actions"><div><span class="eyebrow">Transfers</span><h1 class="page-title">Torrents</h1><SectionBreadcrumbs /><p class="page-description">Add magnets and .torrent files, manage priority, and inspect download state.</p></div><div class="button-row"><button class="btn btn-secondary" :disabled="!torrents.torrents.length" :title="!torrents.torrents.length ? 'No torrents to pause' : undefined" @click="torrents.pauseAll()">Pause all</button><button class="btn btn-secondary" :disabled="!torrents.torrents.length" :title="!torrents.torrents.length ? 'No torrents to resume' : undefined" @click="torrents.resumeAll()">Resume all</button><button class="btn btn-secondary" @click="torrents.load()"><IconGlyph name="refresh" :size="16" /> Refresh</button></div></div>
  <AddTorrentPanel />
  <div class="library-toolbar"><div class="search-field"><IconGlyph name="search" :size="17" /><input v-model="torrents.search" class="input" aria-label="Search torrents" placeholder="Search torrents" /></div><div class="segmented-control"><button v-for="f in ['all','active','completed','stopped']" :key="f" :class="{ active: torrents.filter === f }" @click="torrents.filter = f as any">{{ f }}</button></div><span class="status-badge neutral">{{ torrents.filtered.length }} shown</span></div>
  <div v-if="torrents.loading" class="loading-state">Loading torrents…</div>
  <div v-else-if="torrents.error" class="inline-message error-message"><div><strong>Torrents could not load</strong><p>{{ torrents.error }} · Expected GET /api/torrents</p></div><button class="btn btn-secondary" @click="torrents.load()">Retry</button></div>
  <div v-else-if="!torrents.filtered.length" class="friendly-empty"><strong>{{ torrents.search ? 'No matching torrents' : 'No torrents yet' }}</strong><p>Add a magnet link or upload a .torrent file above.</p></div>
  <article v-for="t in torrents.filtered" v-else :key="t.id" class="card">
    <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap"><div style="min-width:220px;flex:1"><div style="display:flex;align-items:center;gap:8px"><strong>{{ t.name }}</strong><span class="status-badge" :class="t.status === 'downloading' ? 'accent' : t.progress >= 1 ? 'success' : 'neutral'">{{ t.status }}</span></div><div class="progress-track"><div class="progress-bar" :style="{ width: `${Math.max(0, Math.min(100, t.progress * 100))}%` }" /></div><div class="path-text">{{ Math.round(t.progress * 100) }}% · ↓ {{ formatBytes(t.downloadSpeed || 0) }}/s · ↑ {{ formatBytes(t.uploadSpeed || 0) }}/s</div></div><div class="button-row"><button class="btn btn-secondary btn-sm" :disabled="['paused','stopped'].includes(t.status)" :title="['paused','stopped'].includes(t.status) ? 'Torrent is already paused' : undefined" @click="torrents.pause(t.id)">Pause</button><button class="btn btn-secondary btn-sm" :disabled="!['paused','stopped'].includes(t.status)" :title="!['paused','stopped'].includes(t.status) ? 'Torrent is already running' : undefined" @click="torrents.resume(t.id)">Resume</button><select class="select" style="width:auto;min-height:32px;padding:5px 8px" aria-label="Torrent priority" @change="torrents.setPriority(t.id, ($event.target as HTMLSelectElement).value)"><option value="normal">Normal priority</option><option value="high">High priority</option><option value="low">Low priority</option></select><button class="btn btn-secondary btn-sm" @click="info(t)">Info</button><button class="btn btn-secondary btn-sm" @click="remove(t)">Remove</button><button class="btn btn-danger btn-sm" @click="remove(t, true)">Remove + data</button></div></div>
  </article>
  <div v-if="selectedInfo" class="modal-backdrop" @click="selectedInfo = null"><div class="modal" @click.stop><div class="page-actions"><div><span class="eyebrow">Torrent details</span><h2 style="margin:3px 0">{{ selectedName }}</h2></div><button class="icon-button" aria-label="Close torrent info" @click="selectedInfo = null"><IconGlyph name="close" /></button></div><pre class="text-preview" style="margin-top:16px">{{ JSON.stringify(selectedInfo, null, 2) }}</pre></div></div>
</section></template>

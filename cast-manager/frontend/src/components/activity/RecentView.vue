<script setup lang="ts">
import { onMounted } from 'vue'
import { useActivityStore } from '../../stores/activityStore'
import { useAppStore } from '../../stores/appStore'
import { useLibraryStore } from '../../stores/libraryStore'
import IconGlyph from '../common/IconGlyph.vue'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'

const activity = useActivityStore(); const app = useAppStore(); const library = useLibraryStore()
onMounted(() => activity.loadRecent())
function openFolder(path: string) { app.setSection('library'); library.load(path.substring(0, path.lastIndexOf('/')) || library.mediaRoot) }
</script>
<template><section class="page-stack"><div class="page-actions"><div><span class="eyebrow">Server history</span><h1 class="page-title">Recent</h1><SectionBreadcrumbs /><p class="page-description">Recent is stored by the server through GET and POST /api/files/recent.</p></div><button class="btn btn-secondary" @click="activity.loadRecent()"><IconGlyph name="refresh" :size="16" /> Refresh</button></div><div v-if="activity.error" class="inline-message error-message"><div><strong>Recent files could not load</strong><p>{{ activity.error }} · Expected GET /api/files/recent</p></div><button class="btn btn-secondary" @click="activity.loadRecent()">Retry</button></div><div v-else-if="activity.loading" class="loading-state">Loading recent files…</div><div v-else-if="!activity.recent.length" class="friendly-empty"><strong>No recent files</strong><p>Previewing or casting a file will record it here on the server.</p></div><article v-for="item in activity.recent" :key="item.file_path" class="card" style="display:flex;align-items:center;gap:14px"><span class="kind-mark">{{ (item.file_type || 'file').slice(0,4).toUpperCase() }}</span><div style="min-width:0;flex:1"><strong>{{ item.filename || item.file_path.split('/').pop() }}</strong><div class="path-text">{{ item.file_path }}</div></div><span class="status-badge neutral">{{ item.action || 'opened' }}</span><button class="btn btn-secondary btn-sm" @click="openFolder(item.file_path)">Show in Library</button></article></section></template>

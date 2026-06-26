<script setup lang="ts">
import { onMounted } from 'vue'
import { useActivityStore } from '../../stores/activityStore'
import { useAppStore } from '../../stores/appStore'
import { formatBytes } from '../../utils/files'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'
const activity = useActivityStore(); const app = useAppStore()
onMounted(() => activity.loadTrash())
async function restore(id: number) { try { await activity.restoreTrash(id); app.toast('Item restored', 'success') } catch (err) { app.toast(err instanceof Error ? err.message : 'Restore failed', 'error') } }
async function remove(id: number) { if (!confirm('Permanently delete this item? This cannot be undone.')) return; try { await activity.deleteTrash(id); app.toast('Permanently deleted', 'success') } catch (err) { app.toast(err instanceof Error ? err.message : 'Delete failed', 'error') } }
</script>
<template><section class="page-stack"><div class="page-actions"><div><span class="eyebrow">Recovery</span><h1 class="page-title">Trash</h1><SectionBreadcrumbs /><p class="page-description">Restore files safely. Permanent deletion always asks for confirmation.</p></div><button class="btn btn-secondary" @click="activity.loadTrash()">Refresh</button></div><div v-if="activity.error" class="inline-message error-message"><div><strong>Trash could not load</strong><p>{{ activity.error }} · Expected GET /api/files/trash</p></div><button class="btn btn-secondary" @click="activity.loadTrash()">Retry</button></div><div v-else-if="!activity.trash.length" class="friendly-empty"><strong>Trash is empty</strong><p>Files moved to Trash from the Library will appear here.</p></div><article v-for="item in activity.trash" :key="item.id" class="card" style="display:flex;align-items:center;gap:14px"><span class="kind-mark">TRASH</span><div style="min-width:0;flex:1"><strong>{{ item.filename || item.original_path.split('/').pop() }}</strong><div class="path-text">{{ item.original_path }} · {{ formatBytes(item.size) }}</div></div><button class="btn btn-secondary btn-sm" @click="restore(item.id)">Restore</button><button class="btn btn-danger btn-sm" @click="remove(item.id)">Delete forever</button></article></section></template>

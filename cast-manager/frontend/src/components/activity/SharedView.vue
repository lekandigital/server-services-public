<script setup lang="ts">
import { onMounted } from 'vue'
import { useActivityStore } from '../../stores/activityStore'
import { useAppStore } from '../../stores/appStore'
import { copyToClipboard } from '../../utils/clipboard'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'
const activity = useActivityStore(); const app = useAppStore()
onMounted(() => activity.loadShares())
async function copy(id: string) { try { await copyToClipboard(`${location.origin}/s/${id}`); app.toast('Share link copied', 'success') } catch (err) { app.logDiagnostic('clipboard', 'Could not copy share link', err); app.toast('Could not copy share link', 'error') } }
async function revoke(id: string) { if (!confirm('Revoke this share link? Anyone using it will lose access.')) return; await activity.revokeShare(id); app.toast('Share revoked', 'success') }
</script>
<template><section class="page-stack"><div class="page-actions"><div><span class="eyebrow">Access control</span><h1 class="page-title">Shared</h1><SectionBreadcrumbs /><p class="page-description">Links created from Library file actions. Revoke access whenever you need.</p></div><button class="btn btn-secondary" @click="activity.loadShares()">Refresh</button></div><div v-if="activity.error" class="inline-message error-message"><div><strong>Shares could not load</strong><p>{{ activity.error }} · Expected GET /api/shares</p></div><button class="btn btn-secondary" @click="activity.loadShares()">Retry</button></div><div v-else-if="!activity.shares.length" class="friendly-empty"><strong>No active shares</strong><p>Create one from a file's action menu in the Library.</p></div><article v-for="share in activity.shares" :key="share.id" class="card" style="display:flex;align-items:center;gap:14px"><span class="kind-mark">LINK</span><div style="min-width:0;flex:1"><strong>{{ share.filename || share.file_path.split('/').pop() }}</strong><div class="path-text">{{ share.file_path }}</div></div><span class="status-badge neutral">{{ share.permissions || 'view' }}</span><button class="btn btn-secondary btn-sm" @click="copy(share.id)">Copy link</button><button class="btn btn-danger btn-sm" @click="revoke(share.id)">Revoke</button></article></section></template>

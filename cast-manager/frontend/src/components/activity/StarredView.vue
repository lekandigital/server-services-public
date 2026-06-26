<script setup lang="ts">
import { onMounted } from 'vue'
import { useActivityStore } from '../../stores/activityStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { inferFileKind } from '../../utils/files'
import FileActionsMenu from '../library/FileActionsMenu.vue'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'
const activity = useActivityStore(); const library = useLibraryStore()
onMounted(() => activity.loadStarred())
async function open(file: any) { if (inferFileKind(file) === 'folder') await library.navigateToPath(file.path); else library.previewFile(file) }
async function openLocation(file: any) { const parent = file.path.substring(0, file.path.lastIndexOf('/')) || library.mediaRoot; await library.navigateToPath(parent) }
async function unstar(file: any) { await library.toggleStar(file); if (!file.starred) activity.starred = activity.starred.filter((item) => item.path !== file.path) }
</script>
<template><section class="page-stack" data-testid="starred-page"><div class="page-actions"><div><span class="eyebrow">Saved media</span><h1 class="page-title">Starred</h1><SectionBreadcrumbs /><p class="page-description">Files and folders you chose to keep close.</p></div><button class="btn btn-secondary" @click="activity.loadStarred()">Refresh</button></div><div v-if="activity.error" class="inline-message error-message"><div><strong>Starred items could not load</strong><p>{{ activity.error }} · Expected GET /api/files/starred</p></div><button class="btn btn-secondary" @click="activity.loadStarred()">Retry</button></div><div v-else-if="!activity.starred.length" class="friendly-empty"><strong>Nothing starred yet</strong><p>Use the visible star beside any file or folder.</p></div><article v-for="file in activity.starred" :key="file.path" class="card starred-row"><span class="kind-mark">{{ inferFileKind(file).slice(0,4).toUpperCase() }}</span><div style="min-width:0;flex:1"><strong>{{ file.name || file.path.split('/').pop() }}</strong><div class="path-text">{{ file.path }}</div></div><button class="btn btn-secondary btn-sm" @click="open(file)">Open</button><button class="btn btn-secondary btn-sm" @click="openLocation(file)">Open location</button><button class="btn btn-quiet btn-sm" @click="unstar(file)">Unstar</button><FileActionsMenu :file="file" /></article></section></template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import Breadcrumbs from './Breadcrumbs.vue'
import RootLandingView from './RootLandingView.vue'
import FileTable from './FileTable.vue'
import FileGrid from './FileGrid.vue'
import UploadOverlay from '../common/UploadOverlay.vue'
import IconGlyph from '../common/IconGlyph.vue'
import { useLibraryStore } from '../../stores/libraryStore'
import { useAppStore } from '../../stores/appStore'
import { uploadDriveFile } from '../../api/drive'
import type { FileKind } from '../../types'

const library = useLibraryStore()
const app = useAppStore()
const creatingFolder = ref(false)
const folderMenuOpen = ref(false)
const folderDragActive = ref(false)
const uploadProgress = ref<Array<{ name: string; percent: number; error?: string }>>([])
const uploading = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  if (!library.config) await library.init()
  if (!library.showRootView && !library.files.length && !library.loading) await library.loadFromRoute()
})

watch(() => library.searchQuery, (q) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => library.search(q), 320)
})

const filters: Array<{ id: FileKind | 'all'; label: string }> = [
  { id: 'all', label: 'All types' }, { id: 'video', label: 'Videos' }, { id: 'audio', label: 'Audio' },
  { id: 'image', label: 'Images' }, { id: 'pdf', label: 'PDFs' }, { id: 'text', label: 'Text & NFO' }, { id: 'subtitle', label: 'Subtitles' },
  { id: 'torrent', label: 'Torrent files' }, { id: 'folder', label: 'Folders' },
]

async function createFolder() {
  const name = prompt('Folder name')?.trim()
  if (!name) return
  creatingFolder.value = true
  try { await library.createFolder(name) }
  finally { creatingFolder.value = false }
}

function message(err: unknown) {
  return err instanceof Error ? err.message : 'Upload failed'
}

async function uploadFiles(files: FileList | File[], target: string) {
  const list = Array.from(files)
  if (!list.length) return
  uploading.value = true
  uploadProgress.value = list.map((f) => ({ name: f.name, percent: 0 }))
  app.toast(`Uploading ${list.length} file${list.length === 1 ? '' : 's'} to ${target}`, 'info')
  for (let i = 0; i < list.length; i += 1) {
    try {
      const saved = await uploadDriveFile(target, list[i], (percent) => { uploadProgress.value[i].percent = percent })
      uploadProgress.value[i].percent = 100
      if (saved.saved_name !== list[i].name) app.toast(`${list[i].name} saved as ${saved.saved_name}`, 'warning')
    } catch (err) {
      uploadProgress.value[i].error = message(err)
      app.toast(`${list[i].name}: ${message(err)}`, 'error')
    }
  }
  if (target === library.currentPath && !library.showRootView) await library.load(undefined, { updateHistory: false })
  else app.toast('Uploaded to folder', 'success')
  uploading.value = false
  setTimeout(() => { uploadProgress.value = [] }, 3000)
}

function handleFolderListDrop(event: DragEvent) {
  event.preventDefault()
  event.stopPropagation()
  folderDragActive.value = false
  if (event.dataTransfer?.files.length) {
    uploadFiles(event.dataTransfer.files, library.currentPath)
  }
}

function handleFolderListDragOver(event: DragEvent) {
  event.preventDefault()
  event.stopPropagation()
  folderDragActive.value = true
}

function handleFolderListDragLeave(event: DragEvent) {
  // Only deactivate if leaving the container itself
  const target = event.relatedTarget as HTMLElement | null
  const container = event.currentTarget as HTMLElement
  if (!target || !container.contains(target)) {
    folderDragActive.value = false
  }
}
</script>

<template>
  <RootLandingView v-if="library.showRootView" />
  <section v-else class="page-stack" data-testid="library-page">
    <div class="page-actions">
      <div><span class="eyebrow">File Manager</span><h1 class="page-title">Files</h1><Breadcrumbs /></div>
      <div class="button-row">
        <button v-if="library.config?.features.newFolder" class="btn btn-primary" :disabled="creatingFolder || library.loading" :title="creatingFolder ? 'Folder creation is already in progress' : (library.loading ? 'Wait for the current folder to finish loading' : undefined)" @click="createFolder">+ New folder</button>
        <button class="btn btn-secondary" :disabled="library.loading" :title="library.loading ? 'Library refresh is already in progress' : undefined" @click="library.load()"><IconGlyph name="refresh" :size="16" /> {{ library.loading ? 'Refreshing…' : 'Refresh' }}</button>
        <button class="btn btn-secondary" :disabled="library.loading" :title="library.loading ? 'Wait for the current folder to finish loading' : undefined" @click="library.copyCurrentFolderUrl">Copy folder URL</button>
        <div style="position:relative"><button class="icon-button" aria-label="More folder actions" :aria-expanded="folderMenuOpen" @click="folderMenuOpen = !folderMenuOpen"><IconGlyph name="more" :size="18" /></button><div v-if="folderMenuOpen" class="file-action-menu"><button class="btn btn-quiet btn-sm" @click="library.copyCurrentFolderUrl(); folderMenuOpen = false">Copy folder URL</button><button class="btn btn-quiet btn-sm" @click="library.load(); folderMenuOpen = false">Refresh folder</button><button class="btn btn-quiet btn-sm" @click="library.goToRoot(); folderMenuOpen = false">File Manager home</button><button class="btn btn-quiet btn-sm" @click="app.setSection('diagnostics'); folderMenuOpen = false">Open diagnostics</button></div></div>
      </div>
    </div>

    <div class="library-toolbar">
      <div class="search-field"><IconGlyph name="search" :size="17" /><input v-model="library.searchQuery" class="input" aria-label="Search files" :placeholder="library.searchScope === 'current' ? 'Search this folder' : 'Search all configured roots'" /></div>
      <div style="display:flex;gap:8px">
        <select class="select" aria-label="Search scope" :value="library.searchScope" @change="library.setSearchScope(($event.target as HTMLSelectElement).value as 'current' | 'global')"><option value="current">This folder</option><option value="global">All files</option></select>
        <select class="select" aria-label="Filter by type" :value="library.filterType" @change="library.setFilterType(($event.target as HTMLSelectElement).value as FileKind | 'all')">
          <option v-for="f in filters" :key="f.id" :value="f.id">{{ f.label }}</option>
        </select>
        <select class="select" aria-label="Sort library" :value="library.sort" @change="library.setSort(($event.target as HTMLSelectElement).value as any)">
          <option value="name">Name</option><option value="date">Recently modified</option><option value="size">Largest first</option><option value="type">File type</option>
        </select>
      </div>
      <div class="segmented-control" aria-label="View mode"><button :class="{ active: library.viewMode === 'list' }" @click="library.setViewMode('list')">List</button><button :class="{ active: library.viewMode === 'grid' }" @click="library.setViewMode('grid')">Grid</button></div>
    </div>

    <div v-if="uploadProgress.length" class="card drive-upload-status" style="margin-bottom:12px">
      <strong>Uploading…</strong>
      <div v-for="item in uploadProgress" :key="item.name" class="drive-progress-row">
        <div><span>{{ item.name }}</span><span :class="item.error ? 'drive-error-text' : 'path-text'">{{ item.error || `${item.percent}%` }}</span></div>
        <div class="progress-track"><div class="progress-bar" :class="{ failed: item.error }" :style="{ width: `${item.error ? 100 : item.percent}%` }" /></div>
      </div>
    </div>

    <div v-if="library.searchError" class="inline-message error-message"><div><strong>Search is unavailable. You can still browse folders.</strong><p>{{ library.searchError }}</p></div><button class="btn btn-secondary" @click="library.search(library.searchQuery)">Retry</button></div>
    <div v-if="library.loading" class="loading-state">Loading {{ library.currentPath }}…</div>
    <div v-else-if="library.error" class="inline-message error-message"><div><strong>This folder could not be opened</strong><p>{{ library.error }} · {{ library.currentPath }}</p></div><div class="button-row"><button class="btn btn-secondary" @click="library.load()">Retry</button><button class="btn btn-quiet" @click="library.goToRoot()">File Manager home</button><button class="btn btn-quiet" @click="app.setSection('diagnostics')">Diagnostics</button></div></div>
    <div v-else-if="!library.displayFiles.length" class="friendly-empty"><div class="empty-icon">0</div><strong>{{ library.searchQuery ? 'No matching files' : 'This folder is empty' }}</strong><p>{{ library.searchQuery ? 'Try another title or file-type filter.' : library.currentPath }}</p><div class="button-row" style="justify-content:center;margin-top:14px"><button class="btn btn-secondary" @click="library.load()">Refresh</button><button v-if="library.config?.features.newFolder" class="btn btn-primary" @click="createFolder">New folder</button></div></div>
    <div
      v-else
      class="file-listing-drop-zone"
      style="position:relative"
      @dragover="handleFolderListDragOver"
      @dragleave="handleFolderListDragLeave"
      @drop="handleFolderListDrop"
    >
      <UploadOverlay :visible="folderDragActive" target-label="Drop to upload to this folder" :target-path="library.currentPath" />
      <FileTable v-if="library.viewMode === 'list'" :files="library.displayFiles" />
      <FileGrid v-else :files="library.displayFiles" />
    </div>
  </section>
</template>

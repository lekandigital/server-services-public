<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAppStore } from '../../stores/appStore'
import {
  copyDrive, deleteDrive, driveDownloadUrl, fetchDriveConfig, listDrive, mkdirDrive, moveDrive,
  previewDriveFile, renameDrive, uploadDriveFile,
} from '../../api/drive'
import type { DriveConfig, DriveEntry, DrivePreview } from '../../api/drive'

const app = useAppStore()
const config = ref<DriveConfig | null>(null)
const currentPath = ref('')
const pathInput = ref('')
const parent = ref<string | null>(null)
const entries = ref<DriveEntry[]>([])
const readable = ref(false)
const writable = ref(false)
const loading = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const showHidden = ref(true)
const sortKey = ref<'name' | 'size' | 'modified' | 'type'>('name')
const sortDirection = ref<'asc' | 'desc'>('asc')
const selectedPath = ref<string | null>(null)
const pageDrag = ref(false)
const folderDragPath = ref<string | null>(null)
const uploadTarget = ref<string | null>(null)
const uploadProgress = ref<Array<{ name: string; percent: number; error?: string }>>([])
const fileInput = ref<HTMLInputElement | null>(null)
const preview = ref<DrivePreview | null>(null)
const previewLoading = ref(false)

const visibleEntries = computed(() => {
  const items = entries.value.filter((entry) => showHidden.value || !entry.is_hidden)
  const direction = sortDirection.value === 'asc' ? 1 : -1
  return [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    let comparison = 0
    if (sortKey.value === 'size') comparison = (a.size || 0) - (b.size || 0)
    else if (sortKey.value === 'modified') comparison = String(a.modified || '').localeCompare(String(b.modified || ''))
    else if (sortKey.value === 'type') comparison = String(a.mime || a.type).localeCompare(String(b.mime || b.type))
    else comparison = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    return comparison * direction
  })
})

const breadcrumbs = computed(() => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split('/').filter(Boolean)
  const crumbs = [{ label: '/', path: '/' }]
  let accumulated = ''
  for (const part of parts) {
    accumulated += `/${part}`
    crumbs.push({ label: part, path: accumulated })
  }
  return crumbs
})

const quickLocations = computed(() => {
  const user = config.value?.current_user || 'o'
  const home = `/home/${user}`
  return [
    { label: 'Drive', path: config.value?.library_path || `${home}/file-manager/drive`, always: true },
    { label: 'Home', path: home, always: true },
    { label: 'Root', path: '/', always: true },
    { label: 'Server Services', path: `${home}/server-services`, always: false },
    { label: 'Downloads', path: `${home}/Downloads`, always: false },
  ]
})

function message(errorValue: unknown) {
  return errorValue instanceof Error ? errorValue.message : 'Filesystem operation failed'
}

async function navigate(target?: string) {
  const requested = (target ?? pathInput.value).trim() || config.value?.library_path || '/'
  loading.value = true
  error.value = null
  try {
    const result = await listDrive(requested)
    currentPath.value = result.path
    pathInput.value = result.path
    parent.value = result.parent
    entries.value = result.entries
    readable.value = result.readable
    writable.value = result.writable
    selectedPath.value = null
    localStorage.setItem('cm_drive_path', result.path)
  } catch (err) {
    error.value = message(err)
  } finally { loading.value = false }
}

async function initialize() {
  try {
    config.value = await fetchDriveConfig()
    await navigate(config.value.library_path)
  } catch (err) { error.value = message(err) }
}

function toggleSort(key: typeof sortKey.value) {
  if (sortKey.value === key) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  else { sortKey.value = key; sortDirection.value = 'asc' }
}

function openEntry(entry: DriveEntry) {
  selectedPath.value = entry.path
  if (entry.is_dir) navigate(entry.path)
  else openPreview(entry)
}

async function createFolder() {
  const name = prompt(`Create a folder in:\n${currentPath.value}`)?.trim()
  if (!name) return
  await runAction(async () => { await mkdirDrive(currentPath.value, name) }, `Created ${name}`)
}

async function renameEntry(entry: DriveEntry) {
  const name = prompt(`Rename:\n${entry.path}\n\nNew name:`, entry.name)?.trim()
  if (!name || name === entry.name) return
  await runAction(async () => { await renameDrive(entry.path, name) }, `Renamed ${entry.name}`)
}

async function copyEntry(entry: DriveEntry) {
  const destination = prompt(`Copy source:\n${entry.path}\n\nDestination path or directory:`, currentPath.value)?.trim()
  if (!destination) return
  await runAction(async () => { await copyDrive(entry.path, destination) }, `Copied ${entry.name}`)
}

async function moveEntry(entry: DriveEntry) {
  const destination = prompt(`Move source:\n${entry.path}\n\nDestination path or directory:`, currentPath.value)?.trim()
  if (!destination) return
  if (!confirm(`Move this item?\n\nSource: ${entry.path}\nDestination: ${destination}\n\nExisting destinations are never overwritten.`)) return
  await runAction(async () => { await moveDrive(entry.path, destination) }, `Moved ${entry.name}`)
}

async function removeEntry(entry: DriveEntry) {
  const warning = entry.is_dir
    ? `Permanently delete this folder and everything inside it?\n\n${entry.path}\n\nThis cannot be undone.`
    : `Permanently delete this ${entry.is_symlink ? 'symlink' : 'file'}?\n\n${entry.path}\n\nThis cannot be undone.`
  if (!confirm(warning)) return
  await runAction(async () => { await deleteDrive(entry.path) }, `Deleted ${entry.name}`)
}

async function runAction(action: () => Promise<void>, success: string) {
  busy.value = true
  try {
    await action()
    app.toast(success, 'success')
    await navigate(currentPath.value)
  } catch (err) { app.toast(message(err), 'error') }
  finally { busy.value = false }
}

async function openPreview(entry: DriveEntry) {
  if (!entry.is_file) {
    app.toast(entry.error || 'This item cannot be previewed', 'warning')
    return
  }
  previewLoading.value = true
  preview.value = null
  try { preview.value = await previewDriveFile(entry.path) }
  catch (err) { app.toast(message(err), 'error') }
  finally { previewLoading.value = false }
}

function closePreview() { preview.value = null; previewLoading.value = false }

async function uploadFiles(files: FileList | File[], target: string) {
  const list = Array.from(files)
  if (!list.length) return
  uploadTarget.value = target
  uploadProgress.value = list.map((file) => ({ name: file.name, percent: 0 }))
  app.toast(`Uploading ${list.length} file${list.length === 1 ? '' : 's'} to ${target}`, 'info')
  for (let index = 0; index < list.length; index += 1) {
    try {
      const saved = await uploadDriveFile(target, list[index], (percent) => { uploadProgress.value[index].percent = percent })
      uploadProgress.value[index].percent = 100
      if (saved.saved_name !== list[index].name) app.toast(`${list[index].name} saved as ${saved.saved_name}`, 'warning')
    } catch (err) {
      uploadProgress.value[index].error = message(err)
      app.toast(`${list[index].name}: ${message(err)}`, 'error')
    }
  }
  if (target === currentPath.value) await navigate(currentPath.value)
  else await navigate(currentPath.value)
  uploadTarget.value = null
}

function handlePageDrop(event: DragEvent) {
  event.preventDefault()
  pageDrag.value = false
  folderDragPath.value = null
  if (event.dataTransfer?.files.length) uploadFiles(event.dataTransfer.files, currentPath.value)
}

function handleFolderDrop(event: DragEvent, entry: DriveEntry) {
  event.preventDefault()
  event.stopPropagation()
  pageDrag.value = false
  folderDragPath.value = null
  if (event.dataTransfer?.files.length) uploadFiles(event.dataTransfer.files, entry.path)
}

function escapeHandler(event: KeyboardEvent) { if (event.key === 'Escape') closePreview() }
onMounted(() => { window.addEventListener('keydown', escapeHandler); initialize() })
onBeforeUnmount(() => window.removeEventListener('keydown', escapeHandler))
</script>

<template>
  <section class="page-stack drive-view" data-testid="drive-page" @dragover.prevent="pageDrag = true" @dragleave.self="pageDrag = false" @drop="handlePageDrop">
    <div class="page-actions">
      <div>
        <span class="eyebrow">File Manager · server storage</span>
        <h1 class="page-title">Drive</h1>
        <p class="page-description">Browse and manage files as {{ config?.current_user || 'the service user' }}. Linux permissions always apply.</p>
      </div>
      <div class="button-row">
        <button class="btn btn-primary" :disabled="busy || loading || !writable" :title="!writable ? 'Current folder is not writable' : undefined" @click="createFolder">+ New folder</button>
        <button class="btn btn-secondary" :disabled="busy || loading || !writable" :title="!writable ? 'Current folder is not writable' : undefined" @click="fileInput?.click()">Upload</button>
        <input ref="fileInput" class="sr-only" type="file" multiple @change="($event.target as HTMLInputElement).files && uploadFiles(($event.target as HTMLInputElement).files!, currentPath)" />
        <button class="btn btn-secondary" :disabled="loading" @click="navigate(currentPath)">{{ loading ? 'Refreshing…' : 'Refresh' }}</button>
      </div>
    </div>

    <div class="drive-quick-nav button-row">
      <button v-for="location in quickLocations" :key="location.label" class="btn btn-secondary btn-sm" @click="navigate(location.path)">{{ location.label }}</button>
      <button v-if="parent" class="btn btn-quiet btn-sm" @click="navigate(parent)">↑ Parent</button>
    </div>

    <form class="drive-path-bar" @submit.prevent="navigate()">
      <label class="sr-only" for="drive-path">Absolute server path</label>
      <input id="drive-path" v-model="pathInput" class="input mono" placeholder="/home/REDACTED_USER/file-manager/drive" autocomplete="off" />
      <button class="btn btn-primary" type="submit" :disabled="loading">Go</button>
    </form>
    <nav class="breadcrumbs" aria-label="Drive breadcrumbs">
      <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
        <span v-if="index">›</span><button @click="navigate(crumb.path)">{{ crumb.label }}</button>
      </template>
    </nav>

    <div class="library-toolbar drive-toolbar">
      <div class="path-text mono">{{ currentPath || 'Loading Drive…' }}</div>
      <label class="drive-hidden-toggle"><input v-model="showHidden" type="checkbox" /> Show hidden files</label>
      <select v-model="sortKey" class="select" aria-label="Sort Drive entries">
        <option value="name">Sort: name</option><option value="size">Sort: size</option><option value="modified">Sort: modified</option><option value="type">Sort: type</option>
      </select>
    </div>

    <div class="drop-zone drive-drop-zone" :class="{ active: pageDrag && !folderDragPath }">
      <strong>Drop files here to upload to the current folder</strong>
      <span class="path-text mono">Target: {{ currentPath }}</span>
    </div>

    <div v-if="uploadProgress.length && (uploadTarget || uploadProgress.some(item => item.error))" class="card drive-upload-status">
      <strong>Upload target: <span class="mono">{{ uploadTarget || currentPath }}</span></strong>
      <div v-for="item in uploadProgress" :key="item.name" class="drive-progress-row">
        <div><span>{{ item.name }}</span><span :class="item.error ? 'drive-error-text' : 'path-text'">{{ item.error || `${item.percent}%` }}</span></div>
        <div class="progress-track"><div class="progress-bar" :class="{ failed: item.error }" :style="{ width: `${item.error ? 100 : item.percent}%` }" /></div>
      </div>
    </div>

    <div v-if="error" class="inline-message error-message">
      <div><strong>This folder could not be opened</strong><p>{{ error }} · {{ pathInput }}</p></div>
      <button class="btn btn-secondary" @click="navigate(config?.library_path)">Open Drive</button>
    </div>
    <div v-else-if="loading" class="loading-state">Reading {{ pathInput }}…</div>
    <div v-else-if="!visibleEntries.length" class="friendly-empty"><strong>This folder is empty</strong><p>{{ currentPath }}</p></div>
    <div v-else class="file-table-wrap">
      <table class="file-table drive-table">
        <thead><tr>
          <th><button class="drive-sort-button" @click="toggleSort('name')">Name</button></th>
          <th><button class="drive-sort-button" @click="toggleSort('type')">Type</button></th>
          <th><button class="drive-sort-button" @click="toggleSort('size')">Size</button></th>
          <th><button class="drive-sort-button" @click="toggleSort('modified')">Modified</button></th>
          <th>Permissions / owner</th><th>Actions</th>
        </tr></thead>
        <tbody>
          <tr v-for="entry in visibleEntries" :key="entry.path" :class="{ selected: selectedPath === entry.path, 'drive-folder-target': folderDragPath === entry.path }" @click="selectedPath = entry.path" @dblclick="openEntry(entry)" @dragover="entry.is_dir && ($event.preventDefault(), $event.stopPropagation(), folderDragPath = entry.path)" @dragleave="entry.is_dir && folderDragPath === entry.path && (folderDragPath = null)" @drop="entry.is_dir && handleFolderDrop($event, entry)">
            <td><button class="file-name-button" :title="entry.path" @click.stop="openEntry(entry)"><span class="drive-entry-icon">{{ entry.is_dir ? '📁' : entry.is_symlink ? '↗' : entry.type === 'special' ? '◆' : '📄' }}</span>{{ entry.name }}</button><div v-if="entry.symlink_target" class="path-text">→ {{ entry.symlink_target }}</div><div v-if="entry.error" class="drive-error-text">{{ entry.error }}</div></td>
            <td><span class="status-badge neutral">{{ entry.mime || entry.type }}</span><span v-if="entry.is_hidden" class="status-badge warning">hidden</span></td>
            <td>{{ entry.size_human }}</td>
            <td>{{ entry.modified ? new Date(entry.modified).toLocaleString() : '—' }}</td>
            <td><span class="mono">{{ entry.permissions }}</span><div class="path-text">{{ entry.owner }}:{{ entry.group }} · {{ entry.readable ? 'R' : '—' }}{{ entry.writable ? 'W' : '—' }}{{ entry.executable ? 'X' : '—' }}</div></td>
            <td><div class="file-row-actions">
              <button v-if="entry.is_dir" class="btn btn-secondary btn-sm" @click.stop="navigate(entry.path)">Open</button>
              <button v-if="entry.is_file" class="btn btn-secondary btn-sm" @click.stop="openPreview(entry)">Preview</button>
              <a v-if="entry.is_file" class="btn btn-secondary btn-sm" :href="driveDownloadUrl(entry.path)">Download</a>
              <button class="btn btn-quiet btn-sm" :disabled="busy" @click.stop="renameEntry(entry)">Rename</button>
              <button class="btn btn-quiet btn-sm" :disabled="busy" @click.stop="copyEntry(entry)">Copy</button>
              <button class="btn btn-quiet btn-sm" :disabled="busy" @click.stop="moveEntry(entry)">Move</button>
              <button class="btn btn-danger btn-sm" :disabled="busy" @click.stop="removeEntry(entry)">Delete</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="previewLoading || preview" class="modal-backdrop" @click="closePreview">
      <article class="modal drive-preview-modal" role="dialog" aria-modal="true" aria-label="File preview" @click.stop>
        <div class="page-actions"><div><span class="eyebrow">Drive preview</span><h2 class="card-title">{{ preview?.metadata.name || 'Loading…' }}</h2><div class="path-text mono">{{ preview?.metadata.path }}</div></div><button class="icon-button" aria-label="Close preview" @click="closePreview">×</button></div>
        <div v-if="previewLoading" class="loading-state">Loading preview…</div>
        <template v-else-if="preview">
          <pre v-if="preview.kind === 'text'" class="text-preview drive-text-preview">{{ preview.content }}</pre>
          <img v-else-if="preview.kind === 'image'" class="drive-image-preview" :src="preview.preview_url" :alt="preview.metadata.name" />
          <iframe v-else-if="preview.kind === 'pdf'" class="drive-frame-preview" :src="preview.preview_url" :title="preview.metadata.name" />
          <audio v-else-if="preview.kind === 'audio'" :src="preview.preview_url" controls />
          <video v-else-if="preview.kind === 'video'" class="drive-video-preview" :src="preview.preview_url" controls />
          <div v-else class="inline-message"><strong>{{ preview.message }}</strong><a class="btn btn-primary" :href="driveDownloadUrl(preview.metadata.path)">Download</a></div>
        </template>
      </article>
    </div>
  </section>
</template>

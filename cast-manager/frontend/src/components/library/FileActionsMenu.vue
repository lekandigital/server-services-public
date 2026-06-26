<script setup lang="ts">
import { computed, ref } from 'vue'
import { inferFileKind } from '../../utils/files'
import { deleteFile, generateStreamToken, trackRecent, renameFile, moveFile } from '../../api/files'
import { createShare } from '../../api/shares'
import { useLibraryStore } from '../../stores/libraryStore'
import { useActivityStore } from '../../stores/activityStore'
import { useAppStore } from '../../stores/appStore'
import { downloadUrl } from '../../api/client'
import { copyToClipboard } from '../../utils/clipboard'
import IconGlyph from '../common/IconGlyph.vue'
import type { FileEntry } from '../../types'

const props = defineProps<{ file: FileEntry }>()
const library = useLibraryStore()
const activity = useActivityStore()
const app = useAppStore()
const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const menuStyle = ref<Record<string, string>>({})
const busy = ref(false)
const kind = computed(() => inferFileKind(props.file))
const castable = computed(() => ['video', 'audio'].includes(kind.value))
const previewable = computed(() => kind.value !== 'folder')
const downloadable = computed(() => kind.value !== 'folder')

function close() { open.value = false }
function toggleMenu() {
  open.value = !open.value
  if (!open.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const width = 224
  const top = Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - 480))
  const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))
  menuStyle.value = { position: 'fixed', top: `${top}px`, left: `${left}px`, width: `${width}px`, maxHeight: `${window.innerHeight - top - 12}px`, overflowY: 'auto' }
}
function preview() { library.previewFile(props.file); recordOpen(); close() }
function openFolder() { library.openFolder(props.file); close() }
function cast() { library.openCastPanel(props.file); close() }

async function run(action: () => Promise<unknown>, success: string) {
  busy.value = true
  try { await action(); app.toast(success, 'success') }
  catch (err) { const message = err instanceof Error ? err.message : 'Action failed'; app.logDiagnostic('file-action', message, { path: props.file.path }); app.toast(message, 'error') }
  finally { busy.value = false; close() }
}

async function share() {
  await run(async () => {
    const data = await createShare(props.file.path)
    const shareUrl = (data as { shareUrl?: string }).shareUrl || ''
    if (!shareUrl) throw new Error('The server did not return a share URL')
    await copyToClipboard(shareUrl)
  }, 'Share link created and copied')
}

async function streamUrlAction() {
  await run(async () => {
    const data = await generateStreamToken(props.file.path)
    const url = (data as { url?: string }).url || ''
    if (!url) throw new Error('The server did not return a stream URL')
    await copyToClipboard(url)
  }, 'Stream URL copied')
}

async function copyAppLink() {
  await run(async () => copyToClipboard(library.appUrl(props.file)), kind.value === 'folder' ? 'Folder URL copied' : 'App link copied')
}

async function toggleStar() {
  busy.value = true
  await library.toggleStar(props.file)
  busy.value = false
  close()
}

async function rename() {
  const next = prompt('New name', props.file.name)?.trim()
  if (!next || next === props.file.name) return
  await run(async () => { await renameFile(props.file.path, next); await library.load() }, 'Renamed')
}

async function move() {
  const destination = prompt('Move to directory', library.mediaRoot)?.trim()
  if (!destination) return
  await run(async () => { await moveFile(props.file.path, destination); await library.load() }, 'Moved')
}

async function trash() {
  if (!confirm(`Move “${props.file.name}” to Trash? You can restore it later.`)) return
  await run(async () => { await deleteFile(props.file.path); await library.load() }, 'Moved to Trash')
}

function download() { window.open(downloadUrl(props.file.path), '_blank', 'noopener'); close() }
function addQueue() { activity.addToQueue({ path: props.file.path, name: props.file.name, type: kind.value }); app.toast('Added to this browser’s queue', 'success'); close() }

async function recordOpen() {
  trackRecent({ path: props.file.path, action: 'open', type: kind.value }).catch((err) => app.logDiagnostic('recent', 'Could not update Recent', err))
}
</script>

<template>
  <div style="position:relative;display:inline-block">
    <button ref="trigger" class="icon-button btn-sm" :aria-label="`More actions for ${file.name}`" :aria-expanded="open" @click.stop="toggleMenu"><IconGlyph name="more" :size="17" /></button>
    <Teleport to="body"><div v-if="open" class="file-action-menu floating-file-menu" :style="menuStyle" @click.stop>
      <button v-if="kind === 'folder'" class="btn btn-quiet btn-sm" @click="openFolder">Open folder</button>
      <button v-else-if="previewable" class="btn btn-quiet btn-sm" @click="preview">{{ kind === 'text' ? 'Read' : kind === 'subtitle' ? 'Inspect subtitle' : 'Preview' }}</button>
      <button v-if="castable" class="btn btn-quiet btn-sm" @click="cast">Cast to device</button>
      <button v-if="castable" class="btn btn-quiet btn-sm" @click="preview">Analyze media</button>
      <button v-if="castable" class="btn btn-quiet btn-sm" @click="addQueue">Add to queue</button>
      <button v-if="castable" class="btn btn-quiet btn-sm" @click="streamUrlAction">Copy stream URL</button>
      <button class="btn btn-quiet btn-sm" :disabled="busy" @click="copyAppLink">{{ kind === 'folder' ? 'Copy folder URL' : 'Copy app link' }}</button>
      <button class="btn btn-quiet btn-sm" :disabled="busy" @click="toggleStar">{{ library.isStarred(file) ? 'Unstar' : 'Star' }}</button>
      <button v-if="kind !== 'folder'" class="btn btn-quiet btn-sm" :disabled="busy" @click="share">Create share link</button>
      <button v-if="downloadable" class="btn btn-quiet btn-sm" @click="download">Download</button>
      <button class="btn btn-quiet btn-sm" :disabled="busy || file.protected" :title="file.protected ? 'Protected paths cannot be renamed' : undefined" @click="rename">Rename</button>
      <button class="btn btn-quiet btn-sm" :disabled="busy || file.protected" :title="file.protected ? 'Protected paths cannot be moved' : undefined" @click="move">Move</button>
      <button class="btn btn-quiet btn-sm" style="color:var(--danger)" :disabled="busy || file.protected" :title="file.protected ? 'Protected paths cannot be trashed' : undefined" @click="trash">Move to Trash</button>
    </div></Teleport>
  </div>
</template>

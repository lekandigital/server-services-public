<script setup lang="ts">
import { ref } from 'vue'
import { useTorrentStore } from '../../stores/torrentStore'
import { useAppStore } from '../../stores/appStore'

const torrents = useTorrentStore()
const app = useAppStore()
const magnet = ref('')
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

async function addMagnet() {
  const lines = magnet.value.split('\n').map((l) => l.trim()).filter(Boolean)
  if (!lines.length) return
  await torrents.addMagnets(lines)
  magnet.value = ''
}

async function onDrop(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file && file.name.endsWith('.torrent')) {
    await torrents.upload(file)
  } else {
    app.toast('Drop a .torrent file', 'warning')
  }
}

async function onFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) await torrents.upload(file)
  ;(e.target as HTMLInputElement).value = ''
}

async function pasteClipboard() {
  try {
    magnet.value = await navigator.clipboard.readText()
  } catch {
    app.toast('Clipboard access denied', 'warning')
  }
}
</script>

<template>
  <div class="card">
    <div
      class="drop-zone"
      :class="{ active: dragging }"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop="onDrop"
    >
      <strong style="display:block;color:var(--text);margin-bottom:4px">Drop a .torrent file here</strong>
      <span>or choose a file from this computer</span>
      <div><button class="btn btn-secondary btn-sm" style="margin-top:10px" @click="fileInput?.click()">Choose .torrent file</button><input ref="fileInput" class="sr-only" type="file" accept=".torrent,application/x-bittorrent" @change="onFile" /></div>
    </div>
    <textarea v-model="magnet" class="input" rows="3" placeholder="Paste magnet link(s)…" style="margin-top:12px" />
    <div class="toolbar" style="margin-top:12px">
      <button class="btn btn-secondary" @click="pasteClipboard">Paste from clipboard</button>
      <button class="btn btn-primary" :disabled="!magnet.trim()" :title="!magnet.trim() ? 'Paste one or more magnet links first' : undefined" @click="addMagnet">Add magnet{{ magnet.includes('\n') ? 's' : '' }}</button>
    </div>
  </div>
</template>

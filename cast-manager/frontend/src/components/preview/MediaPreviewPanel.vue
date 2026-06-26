<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import VideoPlayer from './VideoPlayer.vue'
import ImagePreview from './ImagePreview.vue'
import PdfPreview from './PdfPreview.vue'
import TextPreview from './TextPreview.vue'
import SubtitlePreview from './SubtitlePreview.vue'
import MetadataPanel from './MetadataPanel.vue'
import IconGlyph from '../common/IconGlyph.vue'
import { inferFileKind, formatBytes } from '../../utils/files'
import { useLibraryStore } from '../../stores/libraryStore'
import { useMediaStore } from '../../stores/mediaStore'
import { useAppStore } from '../../stores/appStore'
import { useActivityStore } from '../../stores/activityStore'
import { generateStreamToken, trackRecent } from '../../api/files'
import { createShare } from '../../api/shares'
import { downloadUrl } from '../../api/client'
import { copyToClipboard } from '../../utils/clipboard'
import type { MediaAnalysis } from '../../types'

const library = useLibraryStore()
const media = useMediaStore()
const app = useAppStore()
const activity = useActivityStore()
const browserOk = ref<boolean | null>(null)
const analysis = ref<MediaAnalysis | null>(null)
const analysisError = ref('')
const analyzing = ref(false)

const file = computed(() => library.selected)
const kind = computed(() => (file.value ? inferFileKind(file.value) : 'other'))
const castable = computed(() => ['video', 'audio'].includes(kind.value))
const playbackKind = computed<'video' | 'audio'>(() => kind.value === 'audio' ? 'audio' : 'video')

function browserCompatible(value: MediaAnalysis) {
  const container = String(value.container || '').toLowerCase()
  const video = String(value.videoCodec || '').toLowerCase()
  const audio = String(value.audioCodec || '').toLowerCase()
  if (kind.value === 'audio') return ['mp3', 'mpeg', 'wav', 'ogg', 'webm', 'mp4', 'm4a'].some((x) => container.includes(x)) && !/flac|eac3|dts/.test(audio)
  return ['mp4', 'mov', 'webm'].some((x) => container.includes(x)) && /h264|avc|vp8|vp9|av1/.test(video) && !/hevc|h265|eac3|dts/.test(`${video} ${audio}`)
}

async function analyze() {
  if (!file.value || !castable.value) return
  analyzing.value = true
  analysisError.value = ''
  try {
    await media.loadInfo(file.value.path)
    analysis.value = await media.loadAnalysis(file.value.path, 'browser')
    browserOk.value = browserCompatible(analysis.value)
    await media.loadSubtitles(file.value.path)
  } catch (err) {
    browserOk.value = false
    analysisError.value = err instanceof Error ? err.message : 'Compatibility analysis failed'
  } finally { analyzing.value = false }
}

onMounted(async () => {
  if (!file.value) return
  if (castable.value) await analyze()
  trackRecent({ path: file.value.path, action: kind.value === 'video' ? 'preview' : 'open', type: kind.value }).catch((err) => app.logDiagnostic('recent', 'Could not update Recent', err))
})

function close() { library.closePreview() }
function openCast() { if (file.value) library.openCastPanel(file.value) }
function openFolder() { if (file.value) { close(); library.openFolder(file.value) } }
function openDiagnostics() { close(); app.setSection('diagnostics') }
function download() { if (file.value) window.open(downloadUrl(file.value.path), '_blank', 'noopener') }
async function copyAppLink() {
  if (!file.value) return
  try { await copyToClipboard(library.appUrl(file.value)); app.toast(kind.value === 'folder' ? 'Folder URL copied' : 'App link copied', 'success') }
  catch (err) { app.logDiagnostic('clipboard', 'Could not copy app link', err); app.toast('Could not copy app link', 'error') }
}
async function copyStreamUrl() {
  if (!file.value) return
  try {
    const data = await generateStreamToken(file.value.path)
    if (!data.url) throw new Error('The server did not return a stream URL')
    await copyToClipboard(data.url)
    app.toast('Stream URL copied', 'success')
  } catch (err) { app.toast(err instanceof Error ? err.message : 'Could not create stream URL', 'error') }
}
async function createShareLink() {
  if (!file.value || kind.value === 'folder') return
  try {
    const data = await createShare(file.value.path) as { shareUrl?: string }
    if (!data.shareUrl) throw new Error('The server did not return a share URL')
    await copyToClipboard(data.shareUrl)
    app.toast('Share link created and copied', 'success')
  } catch (err) { app.logDiagnostic('share', 'Could not create share link', err); app.toast(err instanceof Error ? err.message : 'Could not create share link', 'error') }
}
function addQueue() { if (!file.value) return; activity.addToQueue({ path: file.value.path, name: file.value.name, type: kind.value }); app.toast('Added to queue', 'success') }
</script>

<template>
  <div class="drawer-backdrop" @click="close">
    <aside class="preview-panel" role="dialog" aria-modal="true" aria-label="File details" @click.stop>
      <header class="preview-header">
        <div><span class="eyebrow">{{ kind }} details</span><h2>{{ file?.name }}</h2><div class="path-text">{{ file?.path }}</div></div>
        <button class="icon-button" aria-label="Close preview" @click="close"><IconGlyph name="close" /></button>
      </header>
      <div class="preview-content" v-if="file">
        <div class="button-row">
          <button v-if="kind === 'folder'" class="btn btn-primary" @click="openFolder">Open folder</button>
          <button v-if="castable" class="btn btn-primary" @click="openCast"><IconGlyph name="cast" :size="16" /> Cast</button>
          <button v-if="castable" class="btn btn-secondary" @click="addQueue">Add to queue</button>
          <button v-if="castable" class="btn btn-secondary" @click="copyStreamUrl">Copy stream URL</button>
          <button v-if="kind !== 'folder'" class="btn btn-secondary" @click="createShareLink">Create share link</button>
          <button class="btn btn-secondary" @click="copyAppLink">{{ kind === 'folder' ? 'Copy folder URL' : 'Copy app link' }}</button>
          <button class="btn btn-secondary" :class="{ 'starred-action': file.starred }" @click="library.toggleStar(file)"><IconGlyph name="star" :size="16" /> {{ file.starred ? 'Unstar' : 'Star' }}</button>
          <button v-if="kind !== 'folder'" class="btn btn-secondary" @click="download">Download</button>
        </div>

        <ImagePreview v-if="kind === 'image'" :path="file.path" />
        <PdfPreview v-else-if="kind === 'pdf'" :path="file.path" />
        <TextPreview v-else-if="kind === 'text'" :path="file.path" />
        <SubtitlePreview v-else-if="kind === 'subtitle'" :path="file.path" />
        <template v-else-if="castable">
          <div v-if="analyzing" class="loading-state">Checking browser and cast compatibility…</div>
          <div v-else-if="browserOk === false" class="compatibility-card">
            <span class="status-dot warning" /><div><strong>Browser playback may not support this codec.</strong><p>Casting is recommended. Cast Manager will preflight the container, video, audio, and subtitle requirements before starting.</p></div>
          </div>
          <VideoPlayer v-else-if="browserOk" :path="file.path" :kind="playbackKind" />
          <div v-if="analysisError" class="inline-message error-message"><div><strong>Analysis unavailable</strong><p>{{ analysisError }}</p></div><button class="btn btn-secondary" @click="analyze">Retry</button></div>
          <MetadataPanel :path="file.path" :kind="kind" />
        </template>
        <div v-else class="friendly-empty compact"><strong>No preview available</strong><p>This file type cannot be previewed in the browser. You can download it instead.</p><button class="btn btn-secondary" style="margin-top:12px" @click="download">Download</button></div>

        <article class="card panel-card">
          <div class="card-header"><div><span class="eyebrow">File</span><h2>Details</h2></div><span class="status-badge neutral">{{ formatBytes(file.size) }}</span></div>
          <dl class="detail-list"><div><dt>Path</dt><dd class="mono">{{ file.path }}</dd></div><div><dt>Type</dt><dd>{{ kind }}</dd></div><div><dt>Modified</dt><dd>{{ file.mtime ? new Date(file.mtime * 1000).toLocaleString() : '—' }}</dd></div><div><dt>Starred</dt><dd>{{ file.starred ? 'Yes' : 'No' }}</dd></div><div><dt>Browser</dt><dd>{{ castable ? (browserOk ? 'Compatible' : 'Cast recommended') : 'No browser player needed' }}</dd></div></dl>
        </article>
        <div class="button-row">
          <button v-if="castable" class="btn btn-secondary" :disabled="analyzing" @click="analyze">{{ analyzing ? 'Analyzing…' : 'Analyze again' }}</button>
          <button class="btn btn-secondary" @click="copyAppLink">{{ kind === 'folder' ? 'Copy folder URL' : 'Copy app link' }}</button>
          <button class="btn btn-quiet" @click="openDiagnostics">Open diagnostics</button>
        </div>
      </div>
    </aside>
  </div>
</template>

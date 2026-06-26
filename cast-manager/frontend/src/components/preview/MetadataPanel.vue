<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMediaStore } from '../../stores/mediaStore'
import { formatBytes, formatDuration } from '../../utils/files'
import type { FileKind } from '../../types'

const props = defineProps<{ path: string; kind: FileKind }>()
const media = useMediaStore()
const info = ref<Record<string, unknown>>({})
const analysis = ref<Record<string, unknown>>({})
const error = ref('')

async function load() {
  error.value = ''
  try {
    info.value = await media.loadInfo(props.path) as Record<string, unknown>
    analysis.value = await media.loadAnalysis(props.path, 'chromecast') as Record<string, unknown>
  } catch (err) { error.value = err instanceof Error ? err.message : 'Metadata unavailable' }
}
onMounted(load)
</script>

<template>
  <div class="card">
    <h3 class="card-title">Metadata</h3>
    <div v-if="error" class="inline-message error-message"><div><strong>Metadata unavailable</strong><p>{{ error }}</p></div><button class="btn btn-secondary btn-sm" @click="load">Retry</button></div>
    <div v-else class="mono">
      <div>Duration: {{ formatDuration((info.duration as number) || 0) }}</div>
      <div>Container: {{ analysis.container || info.container || '—' }}</div>
      <div>Video: {{ analysis.videoCodec || '—' }}</div>
      <div>Audio: {{ analysis.audioCodec || '—' }}</div>
      <div>Cast mode: {{ analysis.playbackMode || '—' }}</div>
    </div>
    <button class="btn btn-sm" style="margin-top:8px" @click="media.retryThumbnail(path, kind === 'audio' ? 'audio' : 'video')">
      Retry thumbnail
    </button>
  </div>
</template>

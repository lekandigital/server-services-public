<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useCastStore } from '../../stores/castStore'
import { useMediaStore } from '../../stores/mediaStore'
import type { SubtitleItem } from '../../types'

const props = defineProps<{ path: string }>()
const cast = useCastStore()
const media = useMediaStore()
const subs = ref<SubtitleItem[]>([])
const hasImageSubs = ref(false)
const selection = ref('off')
const customSource = ref(cast.customSubtitleSource)
const loading = ref(true)
const error = ref('')

function setSelection(value: string) {
  selection.value = value
  error.value = ''
  if (value.startsWith('id:')) {
    const id = value.slice(3)
    const item = subs.value.find((subtitle) => subtitle.id === id)
    cast.selectedSubtitleId = id
    cast.customSubtitleSource = ''
    cast.subtitleMode = item?.burnInRequired ? 'burn-in' : 'id'
    return
  }
  cast.selectedSubtitleId = null
  if (value === 'custom') {
    cast.subtitleMode = /\.idx(?:$|[?#])/i.test(cast.customSubtitleSource) ? 'burn-in' : 'path'
    return
  }
  cast.customSubtitleSource = ''
  cast.subtitleMode = value
}

function applyCustomSource() {
  const source = customSource.value.trim()
  if (!source) {
    error.value = 'Enter a server file path or a complete HTTP/HTTPS subtitle URL.'
    return
  }
  if (!/\.(srt|ass|ssa|vtt|sub|idx)(?:$|[?#])/i.test(source)) {
    error.value = 'Supported subtitle types: SRT, ASS, SSA, VTT, and VobSub IDX + SUB.'
    return
  }
  if (/^[a-z]+:\/\//i.test(source) && !/^https?:\/\//i.test(source)) {
    error.value = 'Web subtitles must use HTTP or HTTPS.'
    return
  }
  cast.customSubtitleSource = source
  setSelection('custom')
}

onMounted(async () => {
  try {
    subs.value = await media.loadSubtitles(props.path)
    const analysis = await media.loadAnalysis(props.path, 'chromecast')
    const embedded = (analysis as { subtitles?: Array<{ codec?: string }> }).subtitles || []
    hasImageSubs.value = subs.value.some((subtitle) => subtitle.burnInRequired)
      || embedded.some((subtitle) => /pgs|hdmv|dvd_sub|vobsub|xsub/i.test(subtitle.codec || ''))
    if (cast.customSubtitleSource) selection.value = 'custom'
    else if (cast.selectedSubtitleId) selection.value = `id:${cast.selectedSubtitleId}`
    else selection.value = cast.subtitleMode === 'id' || cast.subtitleMode === 'path' || cast.subtitleMode === 'burn-in' ? 'off' : cast.subtitleMode
  } catch (errValue) {
    error.value = errValue instanceof Error ? errValue.message : 'Subtitles could not be loaded.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="card" style="margin-bottom:14px">
    <label for="cast-subtitles">Subtitles</label>
    <select id="cast-subtitles" class="select" :value="selection" :disabled="loading" :title="loading ? 'Subtitle discovery is still running' : undefined" @change="setSelection(($event.target as HTMLSelectElement).value)">
      <option value="off">Off</option>
      <option value="auto">Auto</option>
      <option v-for="subtitle in subs" :key="subtitle.id" :value="`id:${subtitle.id}`">{{ subtitle.label }}{{ subtitle.burnInRequired ? ' · burn-in' : '' }}</option>
      <option value="custom">Custom file path or web URL</option>
    </select>

    <div class="field" style="margin-top:12px">
      <label for="custom-subtitle-source">Subtitle file path or web URL</label>
      <div class="button-row" style="flex-wrap:nowrap">
        <input id="custom-subtitle-source" v-model="customSource" class="input" placeholder="/home/REDACTED_USER/watch_list/movie.srt or https://example.com/movie.srt" @keyup.enter="applyCustomSource" />
        <button class="btn btn-secondary" :disabled="!customSource.trim()" :title="!customSource.trim() ? 'Enter a subtitle path or URL first' : undefined" @click="applyCustomSource">Use subtitle</button>
      </div>
      <span class="field-help">Supports SRT, ASS/SSA, VTT, and VobSub IDX + SUB pairs. VobSub is automatically burned into the video with FFmpeg Live.</span>
    </div>

    <p v-if="error" class="field-help" style="color:var(--danger);margin:8px 0 0">{{ error }}</p>
    <p v-else-if="selection === 'custom'" class="field-help" style="color:var(--success);margin:8px 0 0">Custom subtitle source will be validated during preflight.</p>
    <p v-else-if="!subs.length && hasImageSubs" style="color:var(--warning);font-size:12px;margin-bottom:0">Image-based subtitles detected. Auto uses FFmpeg Live burn-in.</p>
    <p v-else-if="!subs.length && !loading" style="color:var(--text-muted);font-size:12px;margin-bottom:0">No nearby subtitles found. You can still enter a server path or web URL.</p>
  </div>
</template>

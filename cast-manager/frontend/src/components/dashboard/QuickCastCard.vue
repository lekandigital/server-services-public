<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { analyzeUrl, quickStreamUrl, type UrlAnalysis } from '../../api/cast'
import { addTorrents } from '../../api/torrents'
import { useAppStore } from '../../stores/appStore'
import IconGlyph from '../common/IconGlyph.vue'

const app = useAppStore()
const url = ref('')
const analysis = ref<UrlAnalysis | null>(null)
const error = ref('')
const analyzing = ref(false)
const casting = ref(false)

const isMagnet = computed(() => url.value.trim().startsWith('magnet:'))
const canCast = computed(() => !!analysis.value?.supported)

watch(url, () => { analysis.value = null; error.value = '' })

async function analyze() {
  const value = url.value.trim()
  if (!value) { error.value = 'Paste a direct media URL, site URL, or magnet link first.'; return }
  if (isMagnet.value) {
    analysis.value = { kind: 'web-page', supported: true, castMethod: null, message: 'Magnet link detected. It will be added to Torrents instead of cast directly.' }
    return
  }
  if (!/^https?:\/\//i.test(value)) { error.value = 'Quick Cast accepts web URLs. Open local files from the Library.'; return }
  analyzing.value = true
  error.value = ''
  try { analysis.value = await analyzeUrl(value) }
  catch (err) { error.value = err instanceof Error ? err.message : 'The URL could not be analyzed.' }
  finally { analyzing.value = false }
}

async function submit() {
  const value = url.value.trim()
  if (!analysis.value) { await analyze(); return }
  if (!analysis.value.supported) return
  casting.value = true
  try {
    if (isMagnet.value) {
      await addTorrents([value])
      app.toast('Magnet added to Torrents', 'success')
    } else {
      await quickStreamUrl(value)
      app.toast('URL sent to the selected cast device', 'success')
    }
    url.value = ''
    analysis.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Quick Cast failed.'
    app.toast(error.value, 'error')
  } finally { casting.value = false }
}
</script>

<template>
  <article class="card hero-card" data-testid="quick-cast-card">
    <span class="eyebrow">Send something now</span>
    <h2>Quick Cast</h2>
    <p>Paste a direct media URL, a supported site URL, an HLS playlist, or a magnet link. Cast Manager checks it before anything reaches the TV.</p>
    <div class="search-field">
      <IconGlyph name="cast" :size="18" />
      <input v-model="url" class="input" aria-label="Quick Cast URL" placeholder="https://example.com/video.mp4" @keyup.enter="analysis ? submit() : analyze()" />
    </div>
    <div class="button-row" style="margin-top:10px">
      <button class="btn btn-secondary" :disabled="analyzing || !url.trim()" :title="!url.trim() ? 'Paste a URL to analyze' : undefined" @click="analyze"><IconGlyph name="search" :size="16" /> {{ analyzing ? 'Analyzing…' : 'Analyze URL' }}</button>
      <button class="btn btn-primary" :disabled="casting || !canCast" :title="!analysis ? 'Analyze the URL first' : (!canCast ? analysis.message : undefined)" @click="submit">{{ isMagnet ? 'Add torrent' : (casting ? 'Sending…' : 'Cast URL') }}</button>
    </div>
    <div v-if="analysis" class="quick-cast-result" :class="analysis.supported ? 'result-success' : 'result-warning'" role="status">
      <span class="status-dot" :class="analysis.supported ? 'online' : 'warning'" />
      <div><strong>{{ analysis.kind.replaceAll('-', ' ') }}</strong><span>{{ analysis.message }}</span></div>
    </div>
    <div v-if="error" class="inline-message error-message" style="margin-top:12px"><div><strong>Could not analyze this URL</strong><p>{{ error }}</p></div><button class="btn btn-secondary" @click="analyze">Retry</button></div>
  </article>
</template>

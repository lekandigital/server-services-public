<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DeviceSelector from './DeviceSelector.vue'
import BackendSelector from './BackendSelector.vue'
import SubtitleSelector from './SubtitleSelector.vue'
import IconGlyph from '../common/IconGlyph.vue'
import { useLibraryStore } from '../../stores/libraryStore'
import { useCastStore } from '../../stores/castStore'
import { useSettingsStore } from '../../stores/settingsStore'
import { useAppStore } from '../../stores/appStore'
import { trackRecent } from '../../api/files'
import { inferFileKind } from '../../utils/files'

const library = useLibraryStore()
const cast = useCastStore()
const settings = useSettingsStore()
const app = useAppStore()
const preflightResult = ref<Record<string, unknown> | null>(null)
const error = ref('')
const preflighting = ref(false)
const confirmPretranscode = ref(false)
const file = computed(() => library.selected)

onMounted(async () => {
  await Promise.allSettled([cast.refreshDevices(), runPreflight()])
})
function close() { library.castPanelOpen = false }
function diagnostics() { close(); app.setSection('diagnostics') }

async function runPreflight() {
  if (!file.value) return
  error.value = ''; preflighting.value = true
  try { preflightResult.value = await cast.runPreflight(file.value.path) as Record<string, unknown> }
  catch (err) { error.value = err instanceof Error ? err.message : 'Preflight failed'; app.toast(error.value, 'error') }
  finally { preflighting.value = false }
}

async function start() {
  if (!file.value) return
  if (!cast.selectedDevice && !cast.devices.length) { error.value = 'No cast device is available. Refresh devices and choose one before starting.'; return }
  if (cast.backend === 'pretranscode' && settings.allowPretranscode && !confirmPretranscode.value) { confirmPretranscode.value = true; return }
  error.value = ''
  try {
    await cast.startCast(file.value.path, file.value.name)
    trackRecent({ path: file.value.path, action: 'cast', type: inferFileKind(file.value) }).catch((err) => app.logDiagnostic('recent', 'Could not update Recent', err))
    close()
  } catch (err) { error.value = err instanceof Error ? err.message : 'Cast failed to start' }
}
</script>

<template>
  <div class="modal-backdrop" @click="close">
    <div class="modal" role="dialog" aria-modal="true" aria-label="Cast setup" @click.stop>
      <div class="page-actions" style="align-items:flex-start;margin-bottom:18px">
        <div><span class="eyebrow">Preflight before playback</span><h2 style="margin:3px 0 4px">Cast to a device</h2><p style="color:var(--text-muted);margin:0">{{ file?.name }}</p></div>
        <button class="icon-button" aria-label="Close cast panel" @click="close"><IconGlyph name="close" /></button>
      </div>
      <DeviceSelector />
      <BackendSelector />
      <SubtitleSelector v-if="file" :path="file.path" />
      <div class="card" style="margin-bottom:14px">
        <label for="start-position">Start position</label>
        <select id="start-position" v-model="cast.startPosition" class="select"><option value="beginning">From the beginning</option><option value="resume">Resume from recent position</option><option value="custom">Custom time in seconds</option></select>
        <input v-if="cast.startPosition === 'custom'" v-model.number="cast.customStartSeconds" aria-label="Custom start time in seconds" class="input" type="number" min="0" style="margin-top:8px" />
      </div>
      <div v-if="cast.backend === 'pretranscode' && confirmPretranscode" class="inline-message" style="background:var(--warning-soft);border-color:#f0d39f;margin-bottom:14px"><div><strong>Pretranscode can take a long time.</strong><p>Use it only when live playback cannot support this file.</p></div><button class="btn btn-primary" @click="start">Continue</button></div>
      <div v-if="error" class="inline-message error-message" style="margin-bottom:14px"><div><strong>Cast setup needs attention</strong><p>{{ error }}</p></div><button class="btn btn-secondary" @click="runPreflight">Retry preflight</button></div>
      <div v-if="preflightResult" class="inline-message" style="margin-bottom:14px"><span class="status-dot online" /><div><strong>Preflight completed</strong><p>{{ (preflightResult as any).summary || (preflightResult as any).message || 'The server returned a cast plan.' }}</p></div></div>
      <div class="button-row">
        <button class="btn btn-secondary" :disabled="preflighting" @click="runPreflight">{{ preflighting ? 'Checking…' : 'Analyze & preflight' }}</button>
        <button class="btn btn-primary" :disabled="cast.starting || preflighting || !file" :title="!file ? 'Select a media file first' : undefined" @click="start">{{ cast.starting ? 'Starting cast…' : 'Start cast' }}</button>
        <button class="btn btn-quiet" @click="diagnostics">Diagnostics</button>
      </div>
    </div>
  </div>
</template>

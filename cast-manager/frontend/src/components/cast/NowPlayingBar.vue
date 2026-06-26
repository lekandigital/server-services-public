<script setup lang="ts">
import Scrubber from './Scrubber.vue'
import IconGlyph from '../common/IconGlyph.vue'
import { useCastStore } from '../../stores/castStore'
import { useAppStore } from '../../stores/appStore'
import { formatDuration } from '../../utils/files'

const cast = useCastStore()
const app = useAppStore()

async function togglePlay() { await cast.control(cast.uiState === 'playing' ? 'pause' : 'play') }
async function skipBack() { await cast.seekFinal(Math.max(0, cast.currentTime - 10)) }
async function skipForward() { await cast.seekFinal(Math.min(cast.duration || cast.currentTime + 30, cast.currentTime + 30)) }
</script>

<template>
  <footer class="now-playing" data-testid="now-playing-bar">
    <div>
      <div class="now-playing-header">
        <strong>{{ cast.status?.title || cast.status?.session?.title || 'Preparing cast…' }}</strong>
        <span class="status-badge accent">{{ cast.uiState.replaceAll('_', ' ') }}</span>
        <span class="status-badge neutral">{{ cast.status?.backend || 'auto' }}</span>
        <span class="status-badge neutral">{{ cast.status?.deviceName || cast.selectedDevice?.name || 'selected device' }}</span>
        <span v-if="cast.restartNotice" class="status-badge warning">{{ cast.restartNotice }}</span>
      </div>
      <Scrubber />
      <div class="now-playing-time">{{ formatDuration(cast.currentTime) }} / {{ formatDuration(cast.duration) }}<span v-if="cast.scrubDragging"> · release to seek once</span></div>
    </div>
    <div class="now-playing-controls">
      <button class="btn btn-secondary btn-sm" aria-label="Seek back 10 seconds" :disabled="cast.seekInFlight" @click="skipBack">−10s</button>
      <button class="icon-button btn-primary" :aria-label="cast.uiState === 'playing' ? 'Pause' : 'Play'" @click="togglePlay"><IconGlyph :name="cast.uiState === 'playing' ? 'pause' : 'play'" /></button>
      <button class="btn btn-secondary btn-sm" aria-label="Seek forward 30 seconds" :disabled="cast.seekInFlight" @click="skipForward">+30s</button>
      <label class="sr-only" for="cast-volume">Volume</label><input id="cast-volume" type="range" min="0" max="100" :value="cast.volume" class="scrubber" style="width:86px" @change="cast.setVolume(Number(($event.target as HTMLInputElement).value))" />
      <button class="icon-button" aria-label="Cast diagnostics" @click="app.setSection('diagnostics')"><IconGlyph name="diagnostics" /></button>
      <button class="icon-button btn-danger" aria-label="Stop cast" @click="cast.stop()"><IconGlyph name="stop" /></button>
    </div>
  </footer>
</template>

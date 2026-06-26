<script setup lang="ts">
import { computed } from 'vue'
import { useCastStore } from '../../stores/castStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { useSettingsStore } from '../../stores/settingsStore'

const cast = useCastStore()
const library = useLibraryStore()
const settings = useSettingsStore()

const options = computed(() => {
  const feats = library.config?.features
  const list = [
    { id: 'auto', label: 'Auto' },
    { id: 'direct', label: 'Direct' },
    { id: 'ffmpeg-live', label: 'FFmpeg live' },
  ]
  if (feats?.vlc) list.push({ id: 'vlc', label: 'VLC' })
  if (feats?.hls) list.push({ id: 'hls', label: 'HLS' })
  if (settings.allowPretranscode) list.push({ id: 'pretranscode', label: 'Pretranscode (advanced)' })
  return list
})
</script>

<template>
  <div class="card" style="margin-bottom:14px">
    <label for="cast-backend">Backend</label>
    <select id="cast-backend" class="select" :value="cast.backend" @change="cast.setBackend(($event.target as HTMLSelectElement).value)">
      <option v-for="o in options" :key="o.id" :value="o.id">{{ o.label }}</option>
    </select>
    <p style="color:var(--text-muted);font-size:11px;margin:7px 0 0">Auto is recommended. Direct avoids transcoding; FFmpeg and VLC handle incompatible files.</p>
    <p v-if="!library.config?.features?.hls" style="color:var(--text-muted);font-size:11px;margin:4px 0 0">HLS is hidden because Cast Doctor reports it unavailable.</p>
  </div>
</template>

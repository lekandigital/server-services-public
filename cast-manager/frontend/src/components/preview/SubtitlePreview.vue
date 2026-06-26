<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { readFile } from '../../api/files'
import { getExtension } from '../../utils/files'

const props = defineProps<{ path: string }>()
const preview = ref('')
const ext = getExtension(props.path)

onMounted(async () => {
  if (['.srt', '.vtt', '.ass'].includes(ext)) {
    try {
      const data = await readFile(props.path)
      preview.value = data.content || ''
    } catch {
      preview.value = 'Could not preview subtitle text.'
    }
  }
})
</script>

<template>
  <div class="card">
    <p>Subtitle file <span class="badge">{{ ext }}</span></p>
    <p v-if="['.sub', '.idx'].includes(ext)" style="color:var(--warning)">
      Image-based VobSub may require burn-in for Chromecast.
    </p>
    <pre v-if="preview" class="text-preview">{{ preview }}</pre>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { readFile } from '../../api/files'
import { downloadUrl } from '../../api/client'

const props = defineProps<{ path: string }>()
const content = ref('')
const truncated = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    const data = await readFile(props.path)
    content.value = data.content || ''
    truncated.value = !!data.truncated
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Cannot read file'
  }
})
</script>

<template>
  <div v-if="error" class="error-state">{{ error }}</div>
  <pre v-else class="text-preview">{{ content }}</pre>
  <p v-if="truncated" style="color:var(--text-muted)">
    File truncated for preview.
    <a :href="downloadUrl(path)">Download full file</a>
  </p>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { streamUrl } from '../../api/client'
defineProps<{ path: string }>()
const failed = ref(false)
</script>

<template>
  <div v-if="failed" class="friendly-empty"><strong>Image preview unavailable</strong><p>The file can still be downloaded or shared; no broken image is shown.</p></div>
  <img v-else :src="streamUrl(path, true)" :alt="path" style="max-width:100%;max-height:520px;object-fit:contain;border-radius:12px;background:var(--surface-soft);display:block;margin:auto" @error="failed = true" />
</template>

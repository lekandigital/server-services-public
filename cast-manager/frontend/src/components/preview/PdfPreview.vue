<script setup lang="ts">
import { streamUrl } from '../../api/client'

defineProps<{ path: string }>()
</script>

<template>
  <iframe
    class="pdf-preview-frame"
    :src="streamUrl(path, true)"
    :title="path"
    style="width:100%;height:520px;border:none;border-radius:12px;background:var(--surface-soft)"
  />
</template>

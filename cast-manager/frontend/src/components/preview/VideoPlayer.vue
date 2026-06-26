<script setup lang="ts">
import { streamUrl } from '../../api/client'

defineProps<{ path: string; kind: 'video' | 'audio' }>()
</script>

<template>
  <video
    v-if="kind === 'video'"
    controls
    playsinline
    preload="metadata"
    style="width:100%;max-height:360px;border-radius:8px;background:#000"
    :src="streamUrl(path, false)"
  />
  <audio v-else controls :src="streamUrl(path, false)" style="width:100%" />
</template>

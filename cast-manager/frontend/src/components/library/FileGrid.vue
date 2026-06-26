<script setup lang="ts">
import FileCard from './FileCard.vue'
import type { FileEntry } from '../../types'

defineProps<{ files: FileEntry[] }>()
</script>

<template>
  <div class="file-grid">
    <FileCard v-for="file in files" :key="file.path" :file="file" />
  </div>
</template>

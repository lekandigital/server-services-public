<script setup lang="ts">
import { computed } from 'vue'
import { inferFileKind, getExtension, kindLabel } from '../../utils/files'
import type { FileEntry } from '../../types'

const props = defineProps<{ file: FileEntry }>()
const ext = computed(() => props.file.extension || getExtension(props.file.name))
const label = computed(() => kindLabel(inferFileKind(props.file)))
</script>

<template>
  <span class="badge">{{ ext || label }}</span>
</template>

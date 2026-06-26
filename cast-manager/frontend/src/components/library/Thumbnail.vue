<script setup lang="ts">
import { computed, ref } from 'vue'
import { inferFileKind, getExtension, kindIcon } from '../../utils/files'
import { streamUrl } from '../../api/client'
import { useMediaStore } from '../../stores/mediaStore'
import type { FileEntry } from '../../types'

const props = defineProps<{ file: FileEntry }>()
const media = useMediaStore()
const imageFailed = ref(false)

const kind = computed(() => inferFileKind(props.file))
const thumb = computed(() => media.thumbnails[props.file.path])

const imageSrc = computed(() => {
  if (kind.value === 'image') return streamUrl(props.file.path, true)
  return thumb.value?.url || ''
})

if (kind.value === 'video' || kind.value === 'audio') {
  media.loadThumbnail(props.file.path, kind.value)
}
</script>

<template>
  <div class="thumb">
    <img v-if="!imageFailed && imageSrc && (kind === 'image' || thumb?.state === 'available')" :src="imageSrc" :alt="file.name" @error="imageFailed = true" />
    <span v-else-if="thumb?.state === 'loading'" class="kind-mark">•••</span>
    <span v-else class="kind-mark">{{ kindIcon(kind) }}</span>
  </div>
</template>

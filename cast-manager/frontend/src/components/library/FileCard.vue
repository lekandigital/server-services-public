<script setup lang="ts">
import Thumbnail from './Thumbnail.vue'
import FileTypeBadge from './FileTypeBadge.vue'
import FileActionsMenu from './FileActionsMenu.vue'
import { inferFileKind, formatBytes } from '../../utils/files'
import { useLibraryStore } from '../../stores/libraryStore'
import IconGlyph from '../common/IconGlyph.vue'
import type { FileEntry } from '../../types'

const props = defineProps<{ file: FileEntry }>()
const library = useLibraryStore()

function open() {
  const kind = inferFileKind(props.file)
  if (kind === 'folder') library.openFolder(props.file)
  else library.previewFile(props.file)
}
function select() { library.selectFile(props.file) }
function cast() { library.openCastPanel(props.file) }
</script>

<template>
  <div class="file-card" :class="{ selected: library.selected?.path === file.path }" @click="select" @dblclick="open">
    <Thumbnail :file="file" />
    <div class="file-card-body">
      <div class="file-card-title">{{ file.name }}</div>
      <div class="toolbar" style="margin:8px 0 0">
        <FileTypeBadge :file="file" />
        <span class="mono" style="font-size:11px;color:var(--text-subtle)">{{ formatBytes(file.size) }}</span>
      </div>
      <div class="file-card-actions" @click.stop>
        <button v-if="['video','audio'].includes(inferFileKind(file))" class="btn btn-primary btn-sm" @click="cast"><IconGlyph name="cast" :size="14" /> Cast</button>
        <button class="star-button" :class="{ starred: library.isStarred(file) }" :aria-label="`${library.isStarred(file) ? 'Unstar' : 'Star'} ${file.name}`" @click="library.toggleStar(file)"><IconGlyph name="star" :size="17" /></button>
        <FileActionsMenu :file="file" />
      </div>
    </div>
  </div>
</template>

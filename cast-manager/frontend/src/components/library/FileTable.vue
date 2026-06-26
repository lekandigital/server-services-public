<script setup lang="ts">
import Thumbnail from './Thumbnail.vue'
import FileTypeBadge from './FileTypeBadge.vue'
import FileActionsMenu from './FileActionsMenu.vue'
import { inferFileKind, formatBytes, formatDate } from '../../utils/files'
import { useLibraryStore } from '../../stores/libraryStore'
import IconGlyph from '../common/IconGlyph.vue'
import type { FileEntry } from '../../types'

defineProps<{ files: FileEntry[] }>()
const library = useLibraryStore()

function open(file: FileEntry) {
  const kind = inferFileKind(file)
  if (kind === 'folder') {
    library.openFolder(file)
    return
  }
  library.previewFile(file)
}

function select(file: FileEntry) { library.selectFile(file) }
function cast(file: FileEntry) { library.openCastPanel(file) }

function primaryLabel(file: FileEntry) {
  const kind = inferFileKind(file)
  if (kind === 'folder') return 'Open'
  if (kind === 'video' || kind === 'audio') return 'Preview'
  if (kind === 'image') return 'Preview'
  if (kind === 'text') return 'Read'
  if (kind === 'subtitle') return 'Inspect'
  return 'Details'
}
</script>

<template>
  <div class="file-table-wrap"><table class="file-table">
    <thead>
      <tr>
        <th></th>
        <th>Name</th>
        <th>Type</th>
        <th>Size</th>
        <th>Modified</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="file in files" :key="file.path" :class="{ selected: library.selected?.path === file.path }" @click="select(file)" @dblclick="open(file)">
        <td style="width:56px"><Thumbnail :file="file" /></td>
        <td>
          <button class="file-name-button" @click.stop="open(file)">{{ file.name }}</button>
        </td>
        <td><FileTypeBadge :file="file" /></td>
        <td>{{ formatBytes(file.size) }}</td>
        <td>{{ formatDate(file.mtime) }}</td>
        <td>
          <div class="file-row-actions" @click.stop>
            <button v-if="['video','audio'].includes(inferFileKind(file))" class="btn btn-primary btn-sm" @click="cast(file)"><IconGlyph name="cast" :size="15" /> Cast</button>
            <button class="btn btn-secondary btn-sm" @click="open(file)">{{ primaryLabel(file) }}</button>
            <button class="star-button" :class="{ starred: library.isStarred(file) }" :aria-label="`${library.isStarred(file) ? 'Unstar' : 'Star'} ${file.name}`" @click="library.toggleStar(file)"><IconGlyph name="star" :size="17" /></button>
            <FileActionsMenu :file="file" />
          </div>
        </td>
      </tr>
    </tbody>
  </table></div>
</template>

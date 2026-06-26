<script setup lang="ts">
import { onMounted } from 'vue'
import { useActivityStore } from '../../stores/activityStore'
import { useLibraryStore } from '../../stores/libraryStore'
import { useAppStore } from '../../stores/appStore'

const activity = useActivityStore()
const library = useLibraryStore()
const app = useAppStore()

onMounted(() => activity.loadRecent())

function openRecent(path: string) {
  app.setSection('library')
  library.load(path.substring(0, path.lastIndexOf('/')) || library.mediaRoot)
}
</script>

<template>
  <article class="card">
    <div class="card-header" style="margin:-20px -20px 18px"><div><span class="eyebrow">From your history</span><h2>Continue watching</h2></div><button class="btn btn-quiet btn-sm" @click="app.setSection('recent')">View all</button></div>
    <div v-if="!activity.recent.length" class="friendly-empty compact"><strong>No watch history yet</strong><p>Preview or cast media from the Library and it will appear here.</p></div>
    <div v-else class="file-grid">
      <button
        v-for="item in activity.recent.slice(0, 8)"
        :key="item.file_path"
        class="file-card"
        style="text-align:left"
        @click="openRecent(item.file_path)"
      >
        <div class="thumb"><span class="kind-mark">MEDIA</span></div>
        <div class="file-card-body">
          <div style="font-weight:600">{{ item.filename || item.file_path.split('/').pop() }}</div>
          <div class="mono" style="color:var(--text-subtle);font-size:11px">{{ item.action }}</div>
        </div>
      </button>
    </div>
  </article>
</template>

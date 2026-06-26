<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useLibraryStore } from '../../stores/libraryStore'
import { useAppStore } from '../../stores/appStore'
import { getDiskStats } from '../../api/storage'
import { formatBytes } from '../../utils/files'
import IconGlyph from '../common/IconGlyph.vue'

const library = useLibraryStore()
const app = useAppStore()
const diskFree = ref('—')
const diskTotal = ref('—')

onMounted(async () => {
  if (!library.config) await library.init()
  try {
    const stats = await getDiskStats() as { free?: number; total?: number; totalSpace?: number; usedSpace?: number }
    diskFree.value = formatBytes(stats.free || (stats.totalSpace ? stats.totalSpace - (stats.usedSpace || 0) : 0))
    diskTotal.value = formatBytes(stats.total || stats.totalSpace || 0)
  } catch {
    diskFree.value = '—'
  }
})

const driveRoot = computed(() => library.fileRoots.find((r) => r.id === 'drive'))
const downloadsRoot = computed(() => library.fileRoots.find((r) => r.id === 'downloads' || r.id === 'Downloads'))
const mediaRoot = computed(() => library.fileRoots.find((r) => r.id === 'watch_list'))

interface PinnedCard {
  id: string
  label: string
  description: string
  path: string
  icon: string
  gradient: string
}

const pinnedCards = computed<PinnedCard[]>(() => {
  const cards: PinnedCard[] = []
  if (driveRoot.value) {
    cards.push({ id: 'drive', label: 'Drive', description: 'General files and uploads', path: driveRoot.value.serverPath, icon: 'library', gradient: 'drive-icon' })
  }
  if (downloadsRoot.value) {
    cards.push({ id: 'downloads', label: 'Downloads', description: 'Torrents and downloaded files', path: downloadsRoot.value.serverPath, icon: 'torrents', gradient: 'downloads-icon' })
  }
  if (mediaRoot.value) {
    cards.push({ id: 'media', label: 'Media Library', description: 'Videos, audio, streams, castable media', path: mediaRoot.value.serverPath, icon: 'cast', gradient: 'media-icon' })
  }
  cards.push({ id: 'browse', label: 'Browse Server', description: 'Browse the server filesystem from /', path: '/', icon: 'storage', gradient: 'browse-icon' })
  return cards
})

function openPinnedFolder(card: PinnedCard) {
  library.navigateToBrowse(card.id === 'browse' ? '/' : card.path)
}
</script>

<template>
  <section class="page-stack root-landing" data-testid="root-landing-page">
    <div class="page-actions">
      <div>
        <span class="eyebrow">File Manager</span>
        <h1 class="page-title">Your files and media</h1>
        <p class="page-description">Open a pinned location, or drag and drop files anywhere to upload to Drive.</p>
      </div>
    </div>

    <div class="root-cards">
      <article
        v-for="card in pinnedCards"
        :key="card.id"
        class="card root-card"
        :data-testid="`${card.id}-card`"
        @click="openPinnedFolder(card)"
      >
        <div class="root-card-icon" :class="card.gradient">
          <IconGlyph :name="card.icon" :size="28" />
        </div>
        <div class="root-card-body">
          <h2 class="card-title">{{ card.label }}</h2>
          <p class="root-card-description">{{ card.description }}</p>
          <span class="path-text mono">{{ card.path }}</span>
        </div>
        <div class="root-card-meta">
          <span class="status-badge success">Pinned</span>
        </div>
      </article>
    </div>

    <div class="root-info">
      <article class="health-card">
        <span class="status-dot online" />
        <div><span>Free storage</span><strong>{{ diskFree }}</strong></div>
      </article>
      <article class="health-card">
        <span class="status-dot online" />
        <div><span>Total capacity</span><strong>{{ diskTotal }}</strong></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.root-landing {
  max-width: 860px;
}
.root-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  margin-top: 8px;
}
.root-card {
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
  transition: box-shadow 0.15s ease, transform 0.12s ease;
}
.root-card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  transform: translateY(-1px);
}
.root-card-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.drive-icon {
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: #fff;
}
.downloads-icon {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: #fff;
}
.media-icon {
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
  color: #fff;
}
.root-card-body {
  flex: 1;
  min-width: 0;
}
.browse-icon {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  color: #fff;
}
.root-card-body .card-title {
  margin: 0 0 4px;
  font-size: 17px;
}
.root-card-description {
  color: var(--text-muted);
  font-size: 13px;
  margin: 0 0 6px;
}
.root-card-meta {
  flex-shrink: 0;
}
.root-info {
  display: flex;
  gap: 12px;
  margin-top: 20px;
}
</style>

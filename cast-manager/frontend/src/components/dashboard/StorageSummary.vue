<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getStorageStats } from '../../api/storage'
import { formatBytes } from '../../utils/files'

const stats = ref<Record<string, unknown> | null>(null)
const error = ref('')

async function load() {
  error.value = ''
  stats.value = null
  try {
    stats.value = await getStorageStats() as Record<string, unknown>
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load storage'
  }
}

onMounted(load)
</script>

<template>
  <article class="card">
    <span class="eyebrow">Capacity</span><h2 class="card-title">Storage summary</h2>
    <div v-if="error" class="inline-message error-message"><div><strong>Storage is unavailable</strong><p>{{ error }}</p></div><button class="btn btn-secondary btn-sm" @click="load">Retry</button></div>
    <div v-else-if="!stats" class="loading-state">Loading storage…</div>
    <div v-else class="stats-row">
      <div class="stat-card">
        <div class="stat-value">{{ formatBytes((stats as { usedSpace?: number }).usedSpace) }}</div>
        <div class="stat-label">Used</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ formatBytes((stats as { freeSpace?: number }).freeSpace) }}</div>
        <div class="stat-label">Free</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ formatBytes((stats as { totalSpace?: number }).totalSpace) }}</div>
        <div class="stat-label">Total</div>
      </div>
    </div>
  </article>
</template>

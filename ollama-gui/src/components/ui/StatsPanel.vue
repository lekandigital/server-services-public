<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { IconX, IconChartBar } from '@tabler/icons-vue'
import { db } from '@/services/database'
import { useModelStore } from '@/stores/modelStore'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const models = useModelStore()

const stats = ref({
  totalChats: 0,
  totalMessages: 0,
  totalUserMessages: 0,
  totalAiMessages: 0,
  totalTokens: 0,
  avgTokensPerSec: 0,
  modelUsage: [] as Array<{ model: string; count: number }>,
})

onMounted(async () => {
  const allChats = await db.chats.count()
  const allMessages = await db.messages.toArray()

  let totalTokens = 0
  let totalTokPerSec = 0
  let tokPerSecCount = 0
  const modelCounts = new Map<string, number>()

  for (const msg of allMessages) {
    if (msg.meta?.evalCount) {
      totalTokens += msg.meta.evalCount
    }
    if (msg.meta?.evalCount && msg.meta?.evalDuration) {
      const tps = msg.meta.evalCount / (msg.meta.evalDuration / 1e9)
      totalTokPerSec += tps
      tokPerSecCount++
    }
    if (msg.meta?.model) {
      modelCounts.set(msg.meta.model, (modelCounts.get(msg.meta.model) || 0) + 1)
    }
  }

  stats.value = {
    totalChats: allChats,
    totalMessages: allMessages.length,
    totalUserMessages: allMessages.filter((m) => m.role === 'user').length,
    totalAiMessages: allMessages.filter((m) => m.role === 'assistant').length,
    totalTokens,
    avgTokensPerSec: tokPerSecCount > 0 ? totalTokPerSec / tokPerSecCount : 0,
    modelUsage: Array.from(modelCounts.entries())
      .map(([model, count]) => ({ model, count }))
      .sort((a, b) => b.count - a.count),
  }
})

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toString()
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      @click.self="emit('close')"
    >
      <div class="w-full max-w-lg rounded-xl bg-surface-1 shadow-2xl">
        <div class="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div class="flex items-center gap-2">
            <IconChartBar :size="20" class="text-accent" />
            <h2 class="text-lg font-semibold text-text-primary">Statistics</h2>
          </div>
          <button @click="emit('close')" class="rounded-md p-1 text-text-muted hover:text-text-primary">
            <IconX :size="20" />
          </button>
        </div>

        <div class="space-y-4 p-5">
          <!-- Key metrics -->
          <div class="grid grid-cols-3 gap-3">
            <div class="rounded-lg bg-surface-2 p-3 text-center">
              <div class="text-xl font-bold text-text-primary">{{ stats.totalChats }}</div>
              <div class="text-2xs text-text-muted">Chats</div>
            </div>
            <div class="rounded-lg bg-surface-2 p-3 text-center">
              <div class="text-xl font-bold text-text-primary">{{ formatNumber(stats.totalMessages) }}</div>
              <div class="text-2xs text-text-muted">Messages</div>
            </div>
            <div class="rounded-lg bg-surface-2 p-3 text-center">
              <div class="text-xl font-bold text-text-primary">{{ formatNumber(stats.totalTokens) }}</div>
              <div class="text-2xs text-text-muted">Tokens</div>
            </div>
          </div>

          <!-- Breakdown -->
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded-lg bg-surface-2 p-3">
              <div class="text-sm font-medium text-text-primary">{{ stats.totalUserMessages }}</div>
              <div class="text-2xs text-text-muted">Your messages</div>
            </div>
            <div class="rounded-lg bg-surface-2 p-3">
              <div class="text-sm font-medium text-text-primary">{{ stats.totalAiMessages }}</div>
              <div class="text-2xs text-text-muted">AI responses</div>
            </div>
          </div>

          <div v-if="stats.avgTokensPerSec > 0" class="rounded-lg bg-surface-2 p-3">
            <div class="text-sm font-medium text-text-primary">
              {{ stats.avgTokensPerSec.toFixed(1) }} tok/s
            </div>
            <div class="text-2xs text-text-muted">Average generation speed</div>
          </div>

          <!-- Model usage -->
          <div v-if="stats.modelUsage.length">
            <h3 class="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted">
              Most Used Models
            </h3>
            <div class="space-y-1.5">
              <div
                v-for="m in stats.modelUsage.slice(0, 5)"
                :key="m.model"
                class="flex items-center justify-between rounded px-2 py-1.5 text-xs"
              >
                <span class="truncate text-text-secondary">{{ m.model }}</span>
                <span class="ml-2 flex-shrink-0 text-text-muted">{{ m.count }} msgs</span>
              </div>
            </div>
          </div>

          <!-- Installed models -->
          <div>
            <h3 class="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted">
              Installed Models
            </h3>
            <div class="text-xs text-text-secondary">
              {{ models.models.length }} models &middot;
              {{ models.models.reduce((sum, m) => sum + m.size, 0) / 1024 / 1024 / 1024 | 0 }} GB total
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

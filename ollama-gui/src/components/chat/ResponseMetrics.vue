<script setup lang="ts">
import { computed } from 'vue'
import type { MessageMeta } from '@/types/chat'

const props = defineProps<{
  meta: MessageMeta
}>()

const tokenCount = computed(() => props.meta.evalCount ?? 0)
const tokensPerSecond = computed(() => {
  if (!props.meta.evalCount || !props.meta.evalDuration) return 0
  return props.meta.evalCount / (props.meta.evalDuration / 1e9)
})
const totalSeconds = computed(() => {
  if (!props.meta.totalDuration) return 0
  return props.meta.totalDuration / 1e9
})
</script>

<template>
  <div v-if="tokenCount > 0" class="flex items-center gap-2 text-2xs text-text-muted">
    <span>{{ tokenCount }} tokens</span>
    <span>&middot;</span>
    <span>{{ tokensPerSecond.toFixed(1) }} tok/s</span>
    <span>&middot;</span>
    <span>{{ totalSeconds.toFixed(1) }}s</span>
    <span v-if="meta.model" class="hidden sm:inline">&middot; {{ meta.model }}</span>
  </div>
</template>

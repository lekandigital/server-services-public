<script setup lang="ts">
import { useActivityStore } from '../../stores/activityStore'
import { useAppStore } from '../../stores/appStore'
import SectionBreadcrumbs from '../common/SectionBreadcrumbs.vue'
const activity = useActivityStore(); const app = useAppStore()
function browse() { app.setSection('library') }
</script>
<template><section class="page-stack"><div class="page-actions"><div><span class="eyebrow">Local playback plan</span><h1 class="page-title">Queue</h1><SectionBreadcrumbs /><p class="page-description">Queue is saved in this browser because the backend has no queue endpoint. Add playable files from Library action menus.</p></div><button class="btn btn-secondary" :disabled="!activity.queue.length" :title="!activity.queue.length ? 'Queue is already empty' : undefined" @click="activity.clearQueue()">Clear queue</button></div><div v-if="!activity.queue.length" class="friendly-empty"><strong>Your queue is empty</strong><p>The server does not expose a queue endpoint, but you can still build a reliable local queue from Library media.</p><button class="btn btn-primary" style="margin-top:14px" @click="browse">Browse Library</button></div><div v-for="(item, i) in activity.queue" :key="`${item.path}-${i}`" class="queue-item" :class="{ active: i === activity.queueIndex }"><span class="kind-mark">{{ item.type.slice(0,3).toUpperCase() }}</span><div style="flex:1"><strong>{{ item.name }}</strong><div class="path-text">{{ item.path }}</div></div><button class="btn btn-quiet btn-sm" @click="activity.removeFromQueue(i)">Remove</button></div></section></template>

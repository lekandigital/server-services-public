<script setup lang="ts">
import { useAppStore } from '../../stores/appStore'

const app = useAppStore()
</script>

<template>
  <div class="toast-host">
    <div v-for="t in app.toasts" :key="t.id" class="toast" :class="t.type" @click="app.dismissToast(t.id)">
      {{ t.message }}
    </div>
  </div>
</template>

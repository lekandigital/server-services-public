<script setup lang="ts">
import { useConnectionStore } from '@/stores/connectionStore'

const connection = useConnectionStore()
</script>

<template>
  <div class="fixed bottom-4 right-4 z-50">
    <div
      class="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-2xs shadow-lg"
      :class="{
        'bg-green-900/80 text-green-300': connection.status === 'connected',
        'bg-red-900/80 text-red-300': connection.status === 'disconnected',
        'bg-yellow-900/80 text-yellow-300': connection.status === 'connecting',
      }"
    >
      <span
        class="h-1.5 w-1.5 rounded-full"
        :class="{
          'bg-green-400': connection.status === 'connected',
          'bg-red-400 animate-pulse': connection.status === 'disconnected',
          'bg-yellow-400 animate-pulse': connection.status === 'connecting',
        }"
      />
      {{ connection.status === 'connected' ? 'Connected' : connection.status === 'disconnected' ? 'Disconnected' : 'Connecting...' }}
    </div>
  </div>
</template>

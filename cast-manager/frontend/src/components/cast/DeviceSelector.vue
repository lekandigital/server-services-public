<script setup lang="ts">
import { useCastStore } from '../../stores/castStore'

const cast = useCastStore()

async function select(device: typeof cast.devices[0]) {
  await cast.pickDevice(device)
}
</script>

<template>
  <div class="card" style="margin-bottom:14px">
    <div class="toolbar">
      <label>Device</label>
      <select class="select" style="max-width:280px" :disabled="!cast.devices.length" aria-label="Cast device" @change="select(cast.devices[($event.target as HTMLSelectElement).selectedIndex])">
        <option v-if="!cast.devices.length">No devices found</option>
        <option v-for="d in cast.devices" :key="d.deviceId" :selected="d.selected">{{ d.name }} ({{ d.provider }})</option>
      </select>
      <button class="btn btn-secondary btn-sm" @click="cast.scanDevices()">Refresh devices</button>
    </div>
  </div>
</template>

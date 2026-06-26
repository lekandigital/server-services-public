<script setup lang="ts">
import { ref } from 'vue'
import { useCastStore } from '../../stores/castStore'

const cast = useCastStore()
const dragging = ref(false)
const localValue = ref(0)
let suppressClickUntil = 0

function onInput(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  localValue.value = val
  cast.setScrubDragging(true, val)
}

async function onChange(e: Event) {
  const val = Number((e.target as HTMLInputElement).value)
  dragging.value = false
  suppressClickUntil = Date.now() + 300
  await cast.seekFinal(val)
}

function onPointerDown() {
  dragging.value = true
}

function suppressClick(event: MouseEvent) {
  if (Date.now() < suppressClickUntil) event.preventDefault()
}

const displayValue = () => (cast.scrubDragging ? cast.scrubValue : cast.currentTime)
const max = () => Math.max(cast.duration, 1)
</script>

<template>
  <input
    type="range"
    class="scrubber"
    min="0"
    :max="max()"
    step="1"
    :value="displayValue()"
    @pointerdown="onPointerDown"
    @input="onInput"
    @change="onChange"
    @click="suppressClick"
  />
</template>

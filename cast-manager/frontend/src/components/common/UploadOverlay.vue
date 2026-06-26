<script setup lang="ts">
defineProps<{
  visible: boolean
  targetLabel: string
  targetPath?: string
}>()
</script>

<template>
  <Transition name="fade">
    <div v-if="visible" class="upload-overlay" data-testid="upload-overlay">
      <div class="upload-overlay-content">
        <div class="upload-overlay-icon">⬆</div>
        <strong>{{ targetLabel }}</strong>
        <span v-if="targetPath" class="path-text mono">{{ targetPath }}</span>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.upload-overlay {
  position: absolute;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(59, 130, 246, 0.06);
  border: 3px dashed var(--accent, #3b82f6);
  border-radius: 12px;
  pointer-events: none;
}
.upload-overlay-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 24px 32px;
  background: var(--surface, #fff);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.10);
  text-align: center;
}
.upload-overlay-icon {
  font-size: 32px;
  line-height: 1;
}
.upload-overlay-content strong {
  font-size: 15px;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>

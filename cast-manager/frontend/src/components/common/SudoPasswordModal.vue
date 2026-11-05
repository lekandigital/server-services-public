<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useAppStore } from '../../stores/appStore'
import IconGlyph from './IconGlyph.vue'

const app = useAppStore()
const password = ref('')
const input = ref<HTMLInputElement | null>(null)

watch(
  () => app.sudoPrompt,
  async (prompt) => {
    if (prompt) {
      password.value = ''
      await nextTick()
      input.value?.focus()
    }
  },
)

function submit() {
  if (!password.value) return
  app.submitSudoPassword(password.value)
  password.value = ''
}

function cancel() {
  app.cancelSudoPassword()
  password.value = ''
}
</script>

<template>
  <div v-if="app.sudoPrompt" class="modal-backdrop" @click="cancel">
    <article
      class="modal sudo-modal"
      role="dialog"
      aria-modal="true"
      :aria-label="app.sudoPrompt.title"
      @click.stop
    >
      <div class="page-actions">
        <div>
          <span class="eyebrow">Permission required</span>
          <h2 style="margin:3px 0">{{ app.sudoPrompt.title }}</h2>
        </div>
        <button class="icon-button" aria-label="Cancel" @click="cancel"><IconGlyph name="close" /></button>
      </div>
      <p style="margin:8px 0 16px;color:var(--muted)">{{ app.sudoPrompt.message }}</p>
      <form @submit.prevent="submit">
        <input
          ref="input"
          v-model="password"
          type="password"
          class="input"
          autocomplete="current-password"
          placeholder="Sudo password"
          style="width:100%"
        />
        <p v-if="app.sudoPromptError" style="margin:8px 0 0;color:var(--danger);font-size:13px">{{ app.sudoPromptError }}</p>
        <div class="page-actions" style="margin-top:16px;justify-content:flex-end;gap:8px">
          <button type="button" class="btn btn-quiet" @click="cancel">Cancel</button>
          <button type="submit" class="btn btn-primary" :disabled="!password">Continue</button>
        </div>
      </form>
    </article>
  </div>
</template>

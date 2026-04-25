<script setup lang="ts">
import { ref } from 'vue'
import { IconX, IconDownload, IconTrash, IconRefresh } from '@tabler/icons-vue'
import { useModelStore } from '@/stores/modelStore'
import { useUiStore } from '@/stores/uiStore'
import { showModel, deleteModel, pullModel } from '@/services/ollama'
import type { OllamaShowResponse, OllamaPullResponse } from '@/types/ollama'

const models = useModelStore()
const ui = useUiStore()

const pullName = ref('')
const isPulling = ref(false)
const pullProgress = ref<OllamaPullResponse | null>(null)
const pullAbort = ref<AbortController | null>(null)
const selectedModel = ref<string | null>(null)
const modelDetails = ref<OllamaShowResponse | null>(null)
const loadingDetails = ref(false)
const confirmDelete = ref<string | null>(null)

async function handlePull() {
  if (!pullName.value.trim() || isPulling.value) return
  isPulling.value = true
  pullAbort.value = new AbortController()

  try {
    await pullModel(
      pullName.value.trim(),
      (p) => { pullProgress.value = p },
      pullAbort.value.signal,
    )
    pullName.value = ''
    await models.refresh()
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      console.error('Pull failed:', err)
    }
  } finally {
    isPulling.value = false
    pullProgress.value = null
    pullAbort.value = null
  }
}

function cancelPull() {
  pullAbort.value?.abort()
}

async function handleDelete(name: string) {
  if (confirmDelete.value !== name) {
    confirmDelete.value = name
    setTimeout(() => { confirmDelete.value = null }, 3000)
    return
  }
  try {
    await deleteModel(name)
    await models.refresh()
    confirmDelete.value = null
    if (selectedModel.value === name) {
      selectedModel.value = null
      modelDetails.value = null
    }
  } catch (err) {
    console.error('Delete failed:', err)
  }
}

async function handleShowDetails(name: string) {
  if (selectedModel.value === name) {
    selectedModel.value = null
    modelDetails.value = null
    return
  }
  selectedModel.value = name
  loadingDetails.value = true
  try {
    modelDetails.value = await showModel(name)
  } catch (err) {
    console.error('Show failed:', err)
    modelDetails.value = null
  } finally {
    loadingDetails.value = false
  }
}

function pullPercent(): number {
  if (!pullProgress.value?.total || !pullProgress.value?.completed) return 0
  return Math.round((pullProgress.value.completed / pullProgress.value.total) * 100)
}
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
    <div
      class="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-xl bg-surface-1 shadow-2xl"
    >
      <!-- Header -->
      <div class="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-4">
        <h2 class="text-lg font-semibold text-text-primary">Model Manager</h2>
        <button
          @click="ui.modelManagerOpen = false"
          class="rounded-md p-1 text-text-muted hover:bg-surface-2 hover:text-text-primary"
        >
          <IconX :size="20" />
        </button>
      </div>

      <!-- Pull model -->
      <div class="border-b border-[var(--color-border)] px-5 py-3">
        <div class="flex gap-2">
          <input
            v-model="pullName"
            type="text"
            placeholder="Pull a model (e.g. llama3:8b)"
            :disabled="isPulling"
            @keydown.enter="handlePull"
            class="flex-1 rounded-lg border border-[var(--color-border)] bg-surface-2 px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
          />
          <button
            v-if="isPulling"
            @click="cancelPull"
            class="rounded-lg bg-danger px-3 py-2 text-sm font-medium text-white"
          >
            Cancel
          </button>
          <button
            v-else
            @click="handlePull"
            :disabled="!pullName.trim()"
            class="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            <IconDownload :size="16" />
          </button>
        </div>
        <!-- Pull progress -->
        <div v-if="isPulling && pullProgress" class="mt-2">
          <div class="flex items-center justify-between text-xs text-text-secondary">
            <span>{{ pullProgress.status }}</span>
            <span v-if="pullProgress.total">{{ pullPercent() }}%</span>
          </div>
          <div v-if="pullProgress.total" class="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div
              class="h-full rounded-full bg-accent transition-all"
              :style="{ width: `${pullPercent()}%` }"
            />
          </div>
        </div>
      </div>

      <!-- Model list -->
      <div class="flex-1 overflow-y-auto px-5 py-3">
        <div class="flex items-center justify-between pb-2">
          <span class="text-xs text-text-muted">
            {{ models.models.length }} models installed
          </span>
          <button
            @click="models.refresh()"
            class="rounded p-1 text-text-muted hover:text-text-primary"
          >
            <IconRefresh :size="14" :class="{ 'animate-spin': models.isLoading }" />
          </button>
        </div>

        <div class="space-y-1">
          <div
            v-for="model in models.rankedModels"
            :key="model.name"
            class="rounded-lg border border-[var(--color-border)] transition-colors hover:border-[var(--color-border-hover)]"
          >
            <div
              class="flex cursor-pointer items-center gap-3 px-3 py-2.5"
              @click="handleShowDetails(model.name)"
            >
              <div class="min-w-0 flex-1">
                <div class="text-sm font-medium text-text-primary">
                  {{ model.ranking.stars }} {{ model.name }}
                </div>
                <div class="text-2xs text-text-muted">
                  {{ model.details.parameter_size }} &middot;
                  {{ model.details.quantization_level }} &middot;
                  {{ models.formatSize(model.size) }} &middot;
                  {{ model.details.family }}
                </div>
              </div>
              <button
                @click.stop="handleDelete(model.name)"
                :class="[
                  'rounded p-1.5 text-text-muted transition-colors',
                  confirmDelete === model.name
                    ? 'bg-danger/20 text-danger'
                    : 'hover:bg-surface-2 hover:text-danger',
                ]"
                :title="confirmDelete === model.name ? 'Click again to confirm' : 'Delete model'"
              >
                <IconTrash :size="14" />
              </button>
            </div>

            <!-- Details panel -->
            <div
              v-if="selectedModel === model.name && modelDetails"
              class="border-t border-[var(--color-border)] bg-surface-0 px-3 py-2"
            >
              <div class="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span class="text-text-muted">Format:</span>
                  <span class="ml-1 text-text-secondary">{{ modelDetails.details.format }}</span>
                </div>
                <div>
                  <span class="text-text-muted">Family:</span>
                  <span class="ml-1 text-text-secondary">{{ modelDetails.details.family }}</span>
                </div>
                <div>
                  <span class="text-text-muted">Parameters:</span>
                  <span class="ml-1 text-text-secondary">{{ modelDetails.details.parameter_size }}</span>
                </div>
                <div>
                  <span class="text-text-muted">Quantization:</span>
                  <span class="ml-1 text-text-secondary">{{ modelDetails.details.quantization_level }}</span>
                </div>
              </div>
              <div v-if="modelDetails.parameters" class="mt-2">
                <span class="text-2xs text-text-muted">Parameters:</span>
                <pre class="mt-1 max-h-32 overflow-auto rounded bg-surface-1 p-2 text-2xs text-text-secondary">{{ modelDetails.parameters }}</pre>
              </div>
            </div>
            <div
              v-else-if="selectedModel === model.name && loadingDetails"
              class="border-t border-[var(--color-border)] bg-surface-0 px-3 py-3 text-center text-xs text-text-muted"
            >
              Loading details...
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

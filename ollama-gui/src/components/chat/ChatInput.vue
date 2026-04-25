<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useTextareaAutosize } from '@vueuse/core'
import { IconSend, IconPlayerStop, IconRefresh, IconMicrophone, IconVolume } from '@tabler/icons-vue'
import { useChatStore } from '@/stores/chatStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useModelStore } from '@/stores/modelStore'

const chat = useChatStore()
const settings = useSettingsStore()
const models = useModelStore()

const { textarea, input } = useTextareaAutosize()
const images = ref<string[]>([])
const isListening = ref(false)
const showModelSuggestions = ref(false)
const modelFilter = ref('')

// @model quick switcher
const filteredModels = computed(() => {
  if (!modelFilter.value) return models.rankedModels.slice(0, 5)
  const q = modelFilter.value.toLowerCase()
  return models.rankedModels.filter((m) =>
    m.name.toLowerCase().includes(q),
  ).slice(0, 5)
})

watch(input, (val) => {
  const atMatch = val.match(/@(\S*)$/)
  if (atMatch) {
    showModelSuggestions.value = true
    modelFilter.value = atMatch[1]
  } else {
    showModelSuggestions.value = false
    modelFilter.value = ''
  }
})

function selectModelFromSuggestion(modelName: string) {
  // Replace @query with empty and switch model
  input.value = input.value.replace(/@\S*$/, '').trim()
  showModelSuggestions.value = false
  if (chat.activeChat?.id) {
    chat.switchModel(chat.activeChat.id, modelName)
  }
}

// Token estimate (~4 chars per token)
const estimatedTokens = computed(() => Math.ceil(input.value.length / 4))

async function send() {
  const content = input.value.trim()
  if (!content && !images.value.length) return
  if (chat.isStreaming) return

  const imgs = images.value.length ? [...images.value] : undefined
  input.value = ''
  images.value = []
  showModelSuggestions.value = false
  await chat.sendMessage(content, imgs)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && settings.sendOnEnter) {
    if (showModelSuggestions.value) return
    e.preventDefault()
    send()
  }
  // Up arrow in empty input = edit last user message
  if (e.key === 'ArrowUp' && !input.value.trim()) {
    const lastUserMsg = [...chat.messages].reverse().find((m) => m.role === 'user')
    if (lastUserMsg) {
      e.preventDefault()
      input.value = lastUserMsg.content
    }
  }
}

function handlePaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return

  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault()
      const file = item.getAsFile()
      if (file) readImageFile(file)
    }
  }
}

function handleDrop(e: DragEvent) {
  e.preventDefault()
  const files = e.dataTransfer?.files
  if (!files) return

  for (const file of files) {
    if (file.type.startsWith('image/')) {
      readImageFile(file)
    }
  }
}

function readImageFile(file: File) {
  const reader = new FileReader()
  reader.onload = () => {
    const base64 = (reader.result as string).split(',')[1]
    if (base64) images.value.push(base64)
  }
  reader.readAsDataURL(file)
}

function removeImage(index: number) {
  images.value.splice(index, 1)
}

// Voice input via SpeechRecognition API
function toggleVoiceInput() {
  if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
    return
  }

  if (isListening.value) {
    isListening.value = false
    return
  }

  const SpeechRecognition = (window as unknown as Record<string, unknown>).SpeechRecognition ||
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition
  if (!SpeechRecognition) return

  const recognition = new (SpeechRecognition as new () => SpeechRecognition)()
  recognition.continuous = false
  recognition.interimResults = false
  recognition.lang = 'en-US'

  recognition.onresult = (event: SpeechRecognitionEvent) => {
    const transcript = event.results[0][0].transcript
    input.value += (input.value ? ' ' : '') + transcript
    isListening.value = false
  }

  recognition.onerror = () => {
    isListening.value = false
  }

  recognition.onend = () => {
    isListening.value = false
  }

  isListening.value = true
  recognition.start()
}

// TTS for last AI message
function readAloud() {
  const lastAi = [...chat.messages].reverse().find((m) => m.role === 'assistant')
  if (!lastAi) return

  const utterance = new SpeechSynthesisUtterance(lastAi.content)
  utterance.rate = 1
  utterance.pitch = 1
  window.speechSynthesis.speak(utterance)
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: Event) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
}

interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList
}

interface SpeechRecognitionResultList {
  readonly length: number
  [index: number]: SpeechRecognitionResult
}

interface SpeechRecognitionResult {
  readonly length: number
  [index: number]: SpeechRecognitionAlternative
}

interface SpeechRecognitionAlternative {
  readonly transcript: string
  readonly confidence: number
}
</script>

<template>
  <div
    class="border-t border-[var(--color-border)] bg-surface-1 p-3 safe-area-bottom"
    @dragover.prevent
    @drop="handleDrop"
  >
    <div class="mx-auto max-w-3xl">
      <!-- Image previews -->
      <div v-if="images.length" class="mb-2 flex flex-wrap gap-2">
        <div v-for="(img, i) in images" :key="i" class="relative">
          <img
            :src="`data:image/png;base64,${img}`"
            class="h-16 rounded-lg border border-[var(--color-border)]"
            alt="Attached"
          />
          <button
            @click="removeImage(i)"
            class="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-danger text-[10px] text-white"
          >
            &times;
          </button>
        </div>
      </div>

      <!-- @model suggestions -->
      <div v-if="showModelSuggestions" class="mb-2 rounded-lg border border-[var(--color-border)] bg-surface-2 p-1">
        <button
          v-for="m in filteredModels"
          :key="m.name"
          @click="selectModelFromSuggestion(m.name)"
          class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-text-secondary transition-colors hover:bg-surface-3 hover:text-text-primary"
        >
          <span class="truncate">{{ m.ranking.stars }} {{ m.ranking.reasoning ? '🧠 ' : '' }}{{ m.name }}</span>
          <span class="ml-auto text-2xs text-text-muted">{{ m.details.parameter_size }}</span>
        </button>
        <div v-if="!filteredModels.length" class="px-2 py-1 text-xs text-text-muted">No matching models</div>
      </div>

      <!-- Input row -->
      <div class="flex items-end gap-2">
        <textarea
          ref="textarea"
          v-model="input"
          @keydown="handleKeydown"
          @paste="handlePaste"
          placeholder="Type a message... (@model to switch)"
          :disabled="!chat.activeChat?.model"
          rows="1"
          class="max-h-[40vh] min-h-[40px] flex-1 resize-none rounded-xl border border-[var(--color-border)] bg-surface-2 px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-50"
        />

        <!-- Action buttons -->
        <div class="flex gap-1">
          <!-- Voice input -->
          <button
            @click="toggleVoiceInput"
            :class="[
              'flex h-10 w-10 items-center justify-center rounded-xl border transition-colors',
              isListening
                ? 'border-danger bg-danger/10 text-danger animate-pulse'
                : 'border-[var(--color-border)] text-text-secondary hover:bg-surface-2 hover:text-text-primary',
            ]"
            title="Voice input"
          >
            <IconMicrophone :size="18" />
          </button>

          <!-- TTS -->
          <button
            v-if="chat.hasMessages && !chat.isStreaming"
            @click="readAloud"
            class="flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--color-border)] text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
            title="Read last response aloud"
          >
            <IconVolume :size="18" />
          </button>

          <button
            v-if="chat.isStreaming"
            @click="chat.abortStreaming()"
            class="flex h-10 w-10 items-center justify-center rounded-xl bg-danger text-white transition-colors hover:bg-red-600"
            title="Stop generating"
          >
            <IconPlayerStop :size="18" />
          </button>
          <template v-else>
            <button
              v-if="chat.hasMessages"
              @click="chat.regenerateLastResponse()"
              class="flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--color-border)] text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
              title="Regenerate last response"
            >
              <IconRefresh :size="18" />
            </button>
            <button
              @click="send"
              :disabled="(!input.trim() && !images.length) || !chat.activeChat?.model"
              class="flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-white transition-colors hover:bg-accent-soft disabled:opacity-50"
              title="Send message"
            >
              <IconSend :size="18" />
            </button>
          </template>
        </div>
      </div>

      <!-- Footer hints -->
      <div class="mt-1 flex items-center justify-between text-2xs text-text-muted">
        <span v-if="!chat.activeChat?.model" class="text-warning">
          Select a model to start chatting
        </span>
        <span v-else>
          {{ settings.sendOnEnter ? 'Enter to send, Shift+Enter for newline' : 'Shift+Enter to send' }}
        </span>
        <div class="flex gap-3">
          <span v-if="settings.showTokenCounter && input.length > 0">
            ~{{ estimatedTokens }} tokens
          </span>
          <span v-if="input.length > 0">{{ input.length }} chars</span>
        </div>
      </div>
    </div>
  </div>
</template>

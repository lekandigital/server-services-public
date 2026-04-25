<script setup lang="ts">
import { IconX, IconDownload, IconUpload, IconTrash, IconFileText, IconKeyboard } from '@tabler/icons-vue'
import { useSettingsStore, THEME_PRESETS } from '@/stores/settingsStore'
import { useChatStore } from '@/stores/chatStore'
import { useUiStore } from '@/stores/uiStore'
import { ref } from 'vue'
import type { ThemePreset } from '@/types/settings'

const settings = useSettingsStore()
const chat = useChatStore()
const ui = useUiStore()
const fileInput = ref<HTMLInputElement>()
const confirmWipe = ref(false)
const showShortcuts = ref(false)

async function handleExport() {
  const data = await chat.exportChats()
  const blob = new Blob([data], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `ollama-chats-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

async function handleExportHtml() {
  if (!chat.activeChat || !chat.messages.length) return
  const html = buildChatHtml(chat.activeChat.name, chat.messages)
  const blob = new Blob([html], { type: 'text/html' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${chat.activeChat.name.replace(/[^a-zA-Z0-9]/g, '_')}.html`
  a.click()
  URL.revokeObjectURL(url)
}

function buildChatHtml(title: string, messages: typeof chat.messages): string {
  const msgs = messages.map((m) => {
    const roleClass = m.role === 'user' ? 'user' : m.role === 'assistant' ? 'ai' : 'system'
    const roleLabel = m.role === 'user' ? 'You' : m.role === 'assistant' ? 'AI' : 'System'
    const escaped = m.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    return `<div class="msg ${roleClass}"><div class="role">${roleLabel}</div><div class="content"><pre>${escaped}</pre></div></div>`
  }).join('\n')

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>${title}</title>
<style>
body{font-family:system-ui,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:#0a0f1a;color:#f1f5f9}
.msg{margin:12px 0;padding:12px 16px;border-radius:12px}
.user{background:#1e3a5f;margin-left:20%}
.ai{background:#1a2235;margin-right:10%}
.system{background:#243049;text-align:center;font-style:italic;font-size:0.9em}
.role{font-size:0.75em;font-weight:600;margin-bottom:4px;opacity:0.7}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font-family:inherit}
h1{text-align:center;font-size:1.2em;opacity:0.8}
</style></head><body>
<h1>${title}</h1>
${msgs}
<p style="text-align:center;opacity:0.4;font-size:0.8em;margin-top:40px">Exported from Ollama GUI</p>
</body></html>`
}

function handleImportClick() {
  fileInput.value?.click()
}

async function handleImport(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const text = await file.text()
  await chat.importChats(text)
}

async function handleWipe() {
  if (!confirmWipe.value) {
    confirmWipe.value = true
    setTimeout(() => { confirmWipe.value = false }, 3000)
    return
  }
  await chat.wipeAllData()
  confirmWipe.value = false
}

const shortcuts = [
  { keys: 'Cmd+N', action: 'New chat' },
  { keys: 'Cmd+K', action: 'Command palette' },
  { keys: 'Cmd+D', action: 'Toggle dark mode' },
  { keys: 'Cmd+Shift+S', action: 'Settings' },
  { keys: 'Cmd+Backspace', action: 'Delete chat' },
  { keys: 'Cmd+1-9', action: 'Switch chat' },
  { keys: 'Esc', action: 'Close panel' },
]
</script>

<template>
  <div class="flex h-full flex-col border-l border-[var(--color-border)] bg-surface-1">
    <!-- Header -->
    <div class="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
      <h2 class="text-sm font-semibold text-text-primary">Settings</h2>
      <button
        @click="ui.settingsOpen = false"
        class="rounded-md p-1 text-text-muted hover:bg-surface-2 hover:text-text-primary"
      >
        <IconX :size="18" />
      </button>
    </div>

    <div class="flex-1 space-y-6 overflow-y-auto p-4">
      <!-- Theme -->
      <section>
        <h3 class="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted">
          Theme
        </h3>
        <div class="grid grid-cols-1 gap-1.5">
          <button
            v-for="preset in THEME_PRESETS"
            :key="preset.id"
            @click="settings.setThemePreset(preset.id as ThemePreset)"
            :class="[
              'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
              settings.themePreset === preset.id
                ? 'border-accent bg-accent/10 text-accent'
                : 'border-[var(--color-border)] text-text-secondary hover:bg-surface-2',
            ]"
          >
            <span
              class="h-3 w-3 rounded-full border"
              :class="{
                'border-accent bg-accent': settings.themePreset === preset.id,
                'border-[var(--color-border)]': settings.themePreset !== preset.id,
              }"
            />
            {{ preset.label }}
            <span class="ml-auto text-2xs text-text-muted">{{ preset.mode }}</span>
          </button>
        </div>
        <label class="mt-3 flex items-center justify-between">
          <span class="text-sm text-text-secondary">Follow system dark/light mode</span>
          <input type="checkbox" v-model="settings.followSystemTheme"
            class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
        </label>
      </section>

      <!-- Appearance -->
      <section>
        <h3 class="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted">
          Behavior
        </h3>
        <div class="space-y-3">
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Enable Markdown</span>
            <input type="checkbox" v-model="settings.enableMarkdown"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Show System Messages</span>
            <input type="checkbox" v-model="settings.showSystemMessages"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Show Response Metrics</span>
            <input type="checkbox" v-model="settings.showMetrics"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Show Token Counter</span>
            <input type="checkbox" v-model="settings.showTokenCounter"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Auto-generate Titles</span>
            <input type="checkbox" v-model="settings.autoTitle"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
          <label class="flex items-center justify-between">
            <span class="text-sm text-text-secondary">Send on Enter</span>
            <input type="checkbox" v-model="settings.sendOnEnter"
              class="h-4 w-4 rounded border-[var(--color-border)] bg-surface-2 text-accent focus:ring-accent" />
          </label>
        </div>
      </section>

      <!-- Connection -->
      <section>
        <h3 class="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted">
          Connection
        </h3>
        <div class="space-y-3">
          <label class="block">
            <span class="mb-1 block text-sm text-text-secondary">Ollama API URL</span>
            <input v-model="settings.baseUrl" type="text"
              class="w-full rounded-lg border border-[var(--color-border)] bg-surface-2 px-3 py-2 text-sm text-text-primary focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent" />
          </label>
          <label class="block">
            <span class="mb-1 block text-sm text-text-secondary">
              History Length: {{ settings.historyLength }} messages
            </span>
            <input v-model.number="settings.historyLength" type="range" min="1" max="50" class="w-full accent-accent" />
          </label>
        </div>
      </section>

      <!-- Keyboard Shortcuts -->
      <section>
        <button
          @click="showShortcuts = !showShortcuts"
          class="mb-2 flex w-full items-center gap-2 text-xs font-medium uppercase tracking-wider text-text-muted"
        >
          <IconKeyboard :size="14" />
          Keyboard Shortcuts
        </button>
        <div v-if="showShortcuts" class="space-y-1">
          <div
            v-for="s in shortcuts"
            :key="s.keys"
            class="flex items-center justify-between rounded px-2 py-1 text-xs"
          >
            <span class="text-text-secondary">{{ s.action }}</span>
            <kbd class="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-2xs text-text-muted">
              {{ s.keys }}
            </kbd>
          </div>
        </div>
      </section>

      <!-- Data -->
      <section>
        <h3 class="mb-3 text-xs font-medium uppercase tracking-wider text-text-muted">
          Data
        </h3>
        <div class="space-y-2">
          <button @click="handleExport"
            class="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary">
            <IconDownload :size="16" /> Export Chats (JSON)
          </button>
          <button @click="handleExportHtml"
            class="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
            :disabled="!chat.activeChat">
            <IconFileText :size="16" /> Export Current Chat (HTML)
          </button>
          <button @click="handleImportClick"
            class="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary">
            <IconUpload :size="16" /> Import Chats
          </button>
          <input ref="fileInput" type="file" accept=".json" class="hidden" @change="handleImport" />
          <button @click="handleWipe"
            :class="[
              'flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors',
              confirmWipe
                ? 'border-danger bg-danger/10 text-danger'
                : 'border-[var(--color-border)] text-text-secondary hover:bg-surface-2 hover:text-danger',
            ]">
            <IconTrash :size="16" />
            {{ confirmWipe ? 'Click again to confirm' : 'Wipe All Data' }}
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

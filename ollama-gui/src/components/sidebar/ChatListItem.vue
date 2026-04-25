<script setup lang="ts">
import { ref } from 'vue'
import { IconTrash, IconEdit, IconPin, IconPinFilled, IconCopy } from '@tabler/icons-vue'
import { formatDistanceToNow } from 'date-fns'
import type { Chat } from '@/types/chat'

const props = defineProps<{
  chat: Chat
  active: boolean
}>()

const emit = defineEmits<{
  select: []
  delete: []
  rename: [name: string]
  pin: []
  duplicate: []
}>()

const isEditing = ref(false)
const editName = ref('')

function startRename() {
  editName.value = props.chat.name
  isEditing.value = true
}

function finishRename() {
  if (editName.value.trim() && editName.value !== props.chat.name) {
    emit('rename', editName.value.trim())
  }
  isEditing.value = false
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') finishRename()
  if (e.key === 'Escape') isEditing.value = false
}

const timeAgo = formatDistanceToNow(
  props.chat.lastMessageAt ?? props.chat.createdAt,
  { addSuffix: true },
)
</script>

<template>
  <div
    :class="[
      'group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2.5 text-sm transition-colors',
      active
        ? 'bg-surface-3 text-text-primary'
        : 'text-text-secondary hover:bg-surface-2 hover:text-text-primary',
    ]"
    @click="emit('select')"
  >
    <!-- Pin indicator -->
    <IconPinFilled
      v-if="chat.pinned"
      :size="14"
      class="flex-shrink-0 text-accent"
    />

    <!-- Chat name -->
    <div class="min-w-0 flex-1">
      <input
        v-if="isEditing"
        v-model="editName"
        class="w-full rounded border border-accent bg-surface-2 px-1 py-0.5 text-sm text-text-primary focus:outline-none"
        @blur="finishRename"
        @keydown="handleKeydown"
        @click.stop
        ref="editInput"
      />
      <template v-else>
        <div class="truncate">{{ chat.name }}</div>
        <div class="truncate text-2xs text-text-muted">
          {{ chat.model || 'No model' }} &middot; {{ timeAgo }}
        </div>
      </template>
    </div>

    <!-- Actions -->
    <div class="flex flex-shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
      <button
        @click.stop="emit('pin')"
        class="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
        :title="chat.pinned ? 'Unpin' : 'Pin'"
      >
        <IconPinFilled v-if="chat.pinned" :size="14" />
        <IconPin v-else :size="14" />
      </button>
      <button
        @click.stop="emit('duplicate')"
        class="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
        title="Duplicate"
      >
        <IconCopy :size="14" />
      </button>
      <button
        @click.stop="startRename"
        class="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-text-primary"
        title="Rename"
      >
        <IconEdit :size="14" />
      </button>
      <button
        @click.stop="emit('delete')"
        class="rounded p-1 text-text-muted hover:bg-surface-3 hover:text-danger"
        title="Delete"
      >
        <IconTrash :size="14" />
      </button>
    </div>
  </div>
</template>

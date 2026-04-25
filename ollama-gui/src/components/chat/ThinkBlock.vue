<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Markdown from '@/components/markdown/Markdown.vue'

const props = withDefaults(
  defineProps<{
    content: string
    isStreaming?: boolean
  }>(),
  { isStreaming: false },
)

const expanded = ref(false)

// Auto-collapse when streaming ends
watch(
  () => props.isStreaming,
  (streaming, wasStreaming) => {
    if (wasStreaming && !streaming) {
      expanded.value = false
    }
  },
)

const isExpanded = computed(() => props.isStreaming || expanded.value)
</script>

<template>
  <div class="my-1">
    <!-- Toggle button -->
    <button
      @click="expanded = !expanded"
      class="flex cursor-pointer items-center gap-0.5"
    >
      <span
        class="text-sm font-medium"
        :class="isStreaming ? 'loading-shimmer' : 'text-text-secondary hover:text-text-primary'"
      >
        Thinking
      </span>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="16"
        height="16"
        viewBox="0 0 20 20"
        fill="currentColor"
        class="flex-shrink-0 transition-transform duration-200"
        :class="[
          isExpanded ? 'rotate-90' : '',
          isStreaming ? 'text-text-muted' : 'text-text-muted',
        ]"
      >
        <path
          d="M7.53 3.78a.666.666 0 0 1 .836-.086l.105.085 5.75 5.75c.26.26.26.682 0 .942l-5.75 5.75a.666.666 0 0 1-.942-.942L12.81 10l-5.28-5.28-.085-.104a.666.666 0 0 1 .085-.837"
        />
      </svg>
    </button>

    <!-- Expanded thinking content -->
    <Transition name="think-expand">
      <div v-if="isExpanded" class="mt-2 ml-0.5 border-l-2 border-[var(--color-border)] pl-4">
        <div class="think-prose text-sm leading-relaxed text-text-secondary">
          <Markdown :content="content" />
        </div>
        <span
          v-if="isStreaming"
          class="result-thinking-cursor"
        />
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* ChatGPT-style shimmer animation for "Thinking" text */
@keyframes loading-shimmer {
  0% {
    background-position: -100% 0;
  }
  100% {
    background-position: 250% 0;
  }
}

.loading-shimmer {
  background: var(--color-text-muted)
    linear-gradient(
      90deg,
      var(--color-text-muted) 0%,
      var(--color-text-secondary) 40%,
      var(--color-text-secondary) 60%,
      var(--color-text-muted) 100%
    );
  background-position: -100% top;
  -webkit-text-fill-color: transparent;
  background-repeat: no-repeat;
  background-size: 50% 200%;
  -webkit-background-clip: text;
  background-clip: text;
  animation: loading-shimmer 1.4s infinite;
  display: inline-block;
}

.loading-shimmer:hover {
  -webkit-text-fill-color: var(--color-text-primary);
  background: none;
}

@media (prefers-reduced-motion: reduce) {
  .loading-shimmer {
    animation: none;
  }
}

/* Pulsing dot cursor while thinking */
@keyframes pulseSize {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.25);
  }
}

.result-thinking-cursor {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--color-text-muted);
  animation: pulseSize 1.25s ease-in-out infinite;
  margin-top: 4px;
}

/* Expand/collapse transition */
.think-expand-enter-active,
.think-expand-leave-active {
  transition: all var(--transition-normal);
  overflow: hidden;
}
.think-expand-enter-from,
.think-expand-leave-to {
  max-height: 0;
  opacity: 0;
  margin-top: 0;
}
.think-expand-enter-to,
.think-expand-leave-from {
  max-height: 5000px;
  opacity: 1;
}

/* Subdued markdown styles for thinking content */
.think-prose :deep(p) {
  @apply my-1.5 first:mt-0 last:mb-0;
}
.think-prose :deep(pre) {
  @apply my-2 overflow-x-auto rounded-lg bg-surface-0 p-2 text-xs;
}
.think-prose :deep(code:not(pre code)) {
  @apply rounded bg-surface-0 px-1 py-0.5 text-xs;
}
.think-prose :deep(ul),
.think-prose :deep(ol) {
  @apply my-1.5 pl-4;
}
.think-prose :deep(li) {
  @apply my-0.5;
}
.think-prose :deep(h1),
.think-prose :deep(h2),
.think-prose :deep(h3) {
  @apply my-2 text-sm font-semibold text-text-primary first:mt-0;
}
.think-prose :deep(blockquote) {
  @apply my-1.5 border-l-2 border-[var(--color-border)] pl-3 italic;
}
.think-prose :deep(a) {
  @apply text-accent underline;
}
.think-prose :deep(table) {
  @apply my-2 w-full border-collapse text-xs;
}
.think-prose :deep(th),
.think-prose :deep(td) {
  @apply border border-[var(--color-border)] px-2 py-1 text-left;
}
.think-prose :deep(th) {
  @apply bg-surface-3 font-medium;
}
.think-prose :deep(.code-block) {
  @apply my-2;
}
</style>

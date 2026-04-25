<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
// @ts-expect-error no types for markdown-it-texmath
import texmath from 'markdown-it-texmath'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const props = defineProps<{
  content: string
}>()

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  highlight(str: string, lang: string) {
    const langLabel = lang || 'text'
    const copyId = `code-${Math.random().toString(36).slice(2, 8)}`

    let highlighted: string
    if (lang && hljs.getLanguage(lang)) {
      try {
        highlighted = hljs.highlight(str, { language: lang }).value
      } catch {
        highlighted = md.utils.escapeHtml(str)
      }
    } else {
      highlighted = md.utils.escapeHtml(str)
    }

    const lines = str.split('\n').length
    const showLineNumbers = lines > 10

    return `<div class="code-block group relative my-3">
      <div class="flex items-center justify-between rounded-t-lg bg-[var(--color-surface-3)] px-3 py-1">
        <span class="text-2xs text-text-muted">${langLabel}</span>
        <button
          onclick="(function(btn,id){var t=document.getElementById(id).textContent;try{navigator.clipboard.writeText(t).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy'},2000)})}catch(e){var ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy'},2000)}})(this,'${copyId}')"
          class="text-2xs text-text-muted transition-colors hover:text-text-primary"
        >Copy</button>
      </div>
      <pre class="!mt-0 overflow-x-auto rounded-b-lg rounded-t-none !bg-[var(--color-surface-0)] p-3 ${showLineNumbers ? 'line-numbers' : ''}"><code id="${copyId}" class="text-sm font-mono hljs language-${langLabel}">${highlighted}</code></pre>
    </div>`
  },
})

md.use(texmath, {
  engine: katex,
  delimiters: 'dollars',
  katexOptions: { throwOnError: false },
})

const rendered = computed(() => md.render(props.content))
</script>

<template>
  <div v-html="rendered" />
</template>

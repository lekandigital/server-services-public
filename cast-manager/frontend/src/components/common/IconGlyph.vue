<script setup lang="ts">
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })

const paths: Record<string, string[]> = {
  dashboard: ['M4 13h6V4H4v9Zm10 7h6v-9h-6v9ZM4 20h6v-3H4v3Zm10-13h6V4h-6v3Z'],
  library: ['M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z', 'M4 18.5A2.5 2.5 0 0 1 6.5 16H20'],
  recent: ['M12 8v5l3 2', 'M4.9 5A9 9 0 1 1 3 12', 'M3 5v5h5'],
  star: ['m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 18.3 6.4 21l1.1-6.2L3 10.4l6.2-.9L12 3Z'],
  shared: ['M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1', 'M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1'],
  torrents: ['M12 3v12', 'm7 10 5 5 5-5', 'M5 21h14'],
  queue: ['M8 6h12M8 12h12M8 18h12', 'M4 6h.01M4 12h.01M4 18h.01'],
  playlists: ['M9 18V5l11-2v13', 'M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm11-2a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
  storage: ['M4 6h16v12H4z', 'M8 18v3h8v-3', 'M8 10h8'],
  trash: ['M5 7h14', 'M9 7V4h6v3', 'm7 7 1 13h8l1-13'],
  activity: ['M4 12h4l2-6 4 12 2-6h4'],
  settings: ['M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z', 'M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.2 3.8-.2-.1a1.7 1.7 0 0 0-1.9.3l-.5.3a1.7 1.7 0 0 0-.9 1.6v.1H9.9v-.1a1.7 1.7 0 0 0-.9-1.6l-.5-.3a1.7 1.7 0 0 0-1.9-.3l-.2.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9l-.3-.5a1.7 1.7 0 0 0-1.5-.8h-.2V9.3h.2a1.7 1.7 0 0 0 1.5-.8l.3-.5a1.7 1.7 0 0 0-.3-1.9L4.2 6l2.2-3.8.2.1a1.7 1.7 0 0 0 1.9-.3l.5-.3A1.7 1.7 0 0 0 9.9.1V0h4.2v.1a1.7 1.7 0 0 0 .9 1.6l.5.3a1.7 1.7 0 0 0 1.9.3l.2-.1L19.8 6l-.1.1a1.7 1.7 0 0 0-.3 1.9l.3.5a1.7 1.7 0 0 0 1.5.8h.2v4.4h-.2a1.7 1.7 0 0 0-1.5.8l-.3.5Z'],
  diagnostics: ['M7 3h10v4H7z', 'M5 7h14v14H5z', 'M9 11h6M9 15h6'],
  menu: ['M4 7h16M4 12h16M4 17h16'],
  search: ['m21 21-4.3-4.3', 'M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14Z'],
  refresh: ['M20 11a8 8 0 1 0-2.3 5.7', 'M20 4v7h-7'],
  cast: ['M4 18a2 2 0 0 1 2 2', 'M4 13a7 7 0 0 1 7 7', 'M4 8a12 12 0 0 1 12 12', 'M5 4h14v11'],
  close: ['M6 6l12 12M18 6 6 18'],
  more: ['M5 12h.01M12 12h.01M19 12h.01'],
  play: ['m9 6 9 6-9 6V6Z'],
  pause: ['M9 6h2v12H9zM15 6h2v12h-2z'],
  stop: ['M7 7h10v10H7z'],
  chevron: ['m9 18 6-6-6-6'],
  external: ['M14 4h6v6M20 4l-9 9', 'M19 13v7H4V5h7'],
}
</script>

<template>
  <svg :width="props.size" :height="props.size" viewBox="0 0 24 24" fill="none" aria-hidden="true" class="icon-glyph">
    <path
      v-for="(path, index) in (paths[props.name] || paths.dashboard)"
      :key="index"
      :d="path"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

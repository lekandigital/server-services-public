<script setup lang="ts">
import { useLibraryStore } from '../../stores/libraryStore'
import { FILE_MANAGER_ROOT_SENTINEL } from '../../utils/pathRoutes'

const library = useLibraryStore()

function handleCrumbClick(crumb: { label: string; path: string }, index: number) {
  if (crumb.path === FILE_MANAGER_ROOT_SENTINEL) {
    library.goToRoot()
  } else if (library.browseMode && index === 0) {
    // "Browse Server" label — navigate to browse root
    library.navigateToBrowse('/')
  } else {
    library.navigateToPath(crumb.path)
  }
}
</script>

<template>
  <nav class="breadcrumbs">
    <template v-for="(crumb, i) in library.breadcrumbs" :key="crumb.path + i">
      <button v-if="i < library.breadcrumbs.length - 1" @click="handleCrumbClick(crumb, i)">{{ crumb.label }}</button>
      <span v-else>{{ crumb.label }}</span>
      <span v-if="i < library.breadcrumbs.length - 1" class="breadcrumb-sep">/</span>
    </template>
  </nav>
</template>

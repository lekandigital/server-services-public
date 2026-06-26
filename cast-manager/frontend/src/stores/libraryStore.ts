import { defineStore } from 'pinia'
import { fetchConfig, listFiles, listAnyFiles, searchFiles, starFile, unstarFile, mkdir, DEFAULT_MEDIA_ROOT, DEFAULT_ROOTS } from '../api/files'
import { inferFileKind } from '../utils/files'
import {
  appUrlForServerPath,
  FILE_MANAGER_ROOT_ROUTE,
  FILE_MANAGER_ROOT_SENTINEL,
  fileManagerRouteToServerPath,
  isPathInsideAllowedRoot,
  serverPathToFileManagerRoute,
  isBrowseRoute,
  browseRouteToServerPath,
  serverPathToBrowseRoute,
} from '../utils/pathRoutes'
import { copyToClipboard } from '../utils/clipboard'
import type { AppConfig, FileEntry, FileKind, FileRoot } from '../types'
import { useAppStore } from './appStore'
import { ApiError } from '../api/client'

let searchController: AbortController | null = null
let configPromise: Promise<AppConfig> | null = null

export type SortKey = 'name' | 'date' | 'size' | 'type'
export type ViewMode = 'list' | 'grid'

export const useLibraryStore = defineStore('library', {
  state: () => ({
    config: null as AppConfig | null,
    rootPath: localStorage.getItem('cm_library_path') || DEFAULT_MEDIA_ROOT,
    currentPath: localStorage.getItem('cm_library_path') || DEFAULT_MEDIA_ROOT,
    files: [] as FileEntry[],
    selected: null as FileEntry | null,
    loading: false,
    error: null as string | null,
    searchQuery: '',
    searchResults: [] as FileEntry[],
    searchError: null as string | null,
    searchScope: 'current' as 'current' | 'global',
    sort: (localStorage.getItem('cm_sort') as SortKey) || 'name',
    filterType: (localStorage.getItem('cm_filter_type') || 'all') as FileKind | 'all',
    viewMode: (localStorage.getItem('cm_view_mode') as ViewMode) || 'list',
    previewOpen: false,
    castPanelOpen: false,
    showRootView: false,
    browseMode: false,
  }),
  getters: {
    mediaRoot(state): string {
      return state.config?.mediaRoot || DEFAULT_MEDIA_ROOT
    },
    fileRoots(state): FileRoot[] {
      return state.config?.fileRoots?.length ? state.config.fileRoots : DEFAULT_ROOTS
    },
    driveRoot(state): string {
      const cfg = state.config
      const roots = cfg?.fileRoots?.length ? cfg.fileRoots : DEFAULT_ROOTS
      const driveEntry = roots.find((r) => r.id === 'drive' || r.label === 'Drive')
      if (driveEntry) return driveEntry.serverPath
      return '/home/REDACTED_USER/file-manager/drive'
    },
    breadcrumbs(state): Array<{ label: string; path: string }> {
      if (state.browseMode) {
        const crumbs: Array<{ label: string; path: string }> = [
          { label: 'Browse Server', path: '/' },
        ]
        if (state.currentPath === '/') {
          crumbs.push({ label: '/', path: '/' })
        } else {
          crumbs.push({ label: '/', path: '/' })
          const segments = state.currentPath.split('/').filter(Boolean)
          let acc = ''
          for (const seg of segments) {
            acc = `${acc}/${seg}`
            crumbs.push({ label: seg, path: acc })
          }
        }
        return crumbs
      }
      const root = [...this.fileRoots]
        .filter((candidate) => state.currentPath === candidate.serverPath || state.currentPath.startsWith(`${candidate.serverPath}/`))
        .sort((a, b) => b.serverPath.length - a.serverPath.length)[0]
      if (!root) return [{ label: 'File Manager', path: FILE_MANAGER_ROOT_SENTINEL }]
      const parts = state.currentPath.slice(root.serverPath.length).split('/').filter(Boolean)
      const crumbs: Array<{ label: string; path: string }> = [
        { label: 'File Manager', path: FILE_MANAGER_ROOT_SENTINEL },
        { label: root.label, path: root.serverPath },
      ]
      let acc = root.serverPath
      for (const p of parts) {
        acc = acc.endsWith('/') ? acc + p : `${acc}/${p}`
        crumbs.push({ label: p, path: acc })
      }
      return crumbs
    },
    displayFiles(state): FileEntry[] {
      const source = state.searchQuery.trim() ? state.searchResults : state.files
      let items = source.map((f) => ({ ...f, kind: inferFileKind(f) }))
      if (state.filterType !== 'all') {
        items = items.filter((f) => f.kind === state.filterType)
      }
      const dir = [...items].sort((a, b) => {
        const aDir = a.isDirectory || a.is_directory ? 1 : 0
        const bDir = b.isDirectory || b.is_directory ? 1 : 0
        return bDir - aDir
      })
      const sortFn = {
        name: (a: FileEntry, b: FileEntry) => a.name.localeCompare(b.name),
        date: (a: FileEntry, b: FileEntry) => (b.mtime || 0) - (a.mtime || 0),
        size: (a: FileEntry, b: FileEntry) => (b.size || 0) - (a.size || 0),
        type: (a: FileEntry, b: FileEntry) => (a.extension || '').localeCompare(b.extension || ''),
      }[state.sort]
      return dir.sort((a, b) => {
        const aDir = a.isDirectory || a.is_directory
        const bDir = b.isDirectory || b.is_directory
        if (aDir && !bDir) return -1
        if (!aDir && bDir) return 1
        return sortFn(a, b)
      })
    },
  },
  actions: {
    async init() {
      if (this.config) return
      try {
        configPromise ||= fetchConfig()
        this.config = await configPromise
        const roots = this.config.fileRoots?.length ? this.config.fileRoots : DEFAULT_ROOTS
        const defaultIndex = roots.findIndex((root) => root.id === this.config?.defaultRootId)
        this.config.fileRoots = defaultIndex > 0 ? [roots[defaultIndex], ...roots.filter((_, index) => index !== defaultIndex)] : roots
        // Don't auto-set currentPath if we're on the root view
        if (!this.showRootView) {
          this.currentPath = this.config.mediaRoot
          this.rootPath = this.config.mediaRoot
        }
      } catch {
        this.config = { mediaRoot: DEFAULT_MEDIA_ROOT, fileRoots: DEFAULT_ROOTS, defaultRootId: 'watch_list', serverUrl: '', features: { hls: false, vlc: true, castDoctor: true, diagnostics: true, cast: true, shares: true, starred: true, trash: true, torrents: true, newFolder: true } }
      } finally {
        configPromise = null
      }
    },
    async load(path?: string, options: { updateHistory?: boolean; replace?: boolean } = {}) {
      const target = path ?? this.currentPath
      this.showRootView = false
      if (!this.browseMode && !isPathInsideAllowedRoot(target, this.fileRoots)) {
        this.error = 'This location is outside the configured media and download roots.'
        return
      }
      if (path && options.updateHistory !== false && target !== this.currentPath) {
        await this.navigateToPath(target, { replace: options.replace })
        return
      }
      this.loading = true
      this.error = null
      this.searchQuery = ''
      this.searchResults = []
      try {
        const data = this.browseMode ? await listAnyFiles(target) : await listFiles(target)
        this.currentPath = data.currentPath || data.path || target
        this.rootPath = data.rootPath || data.root || this.mediaRoot
        localStorage.setItem('cm_library_path', this.currentPath)
        this.files = ((data.files as FileEntry[]) || []).map((f) => ({ ...f, kind: inferFileKind(f) }))
      } catch (err) {
        this.error = err instanceof ApiError ? err.message : 'Failed to load directory'
        useAppStore().recordApiError(err as ApiError)
      } finally {
        this.loading = false
      }
    },
    async loadFromRoute() {
      if (!this.config) await this.init()
      const pathname = window.location.pathname

      if (isBrowseRoute(pathname)) {
        this.browseMode = true
        this.showRootView = false
        let serverPath: string
        try {
          serverPath = browseRouteToServerPath(pathname)
        } catch (err) {
          this.error = err instanceof Error ? err.message : 'This browse URL is invalid'
          return
        }
        const wantsPreview = new URLSearchParams(window.location.search).get('preview') === '1'
        const folderPath = wantsPreview
          ? (serverPath.substring(0, serverPath.lastIndexOf('/')) || '/')
          : serverPath
        await this.load(folderPath, { updateHistory: false })
        if (wantsPreview && !this.error) {
          const file = this.files.find((item) => item.path === serverPath)
          if (file) this.selectFile(file)
          else this.error = 'The linked file is not present in this folder.'
        } else {
          this.previewOpen = false
          this.selected = null
        }
        return
      }

      this.browseMode = false
      let target: string
      try {
        target = fileManagerRouteToServerPath(window.location.pathname, this.fileRoots)
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'This folder URL is invalid'
        return
      }
      // Root landing view — show cards, don't load any folder
      if (target === FILE_MANAGER_ROOT_SENTINEL) {
        this.showRootView = true
        this.error = null
        this.loading = false
        return
      }
      this.showRootView = false
      const wantsPreview = new URLSearchParams(window.location.search).get('preview') === '1'
      const folderPath = wantsPreview ? (target.substring(0, target.lastIndexOf('/')) || this.mediaRoot) : target
      await this.load(folderPath, { updateHistory: false })
      if (wantsPreview && !this.error) {
        const file = this.files.find((item) => item.path === target)
        if (file) this.selectFile(file)
        else this.error = 'The linked file is not present in this folder.'
      } else {
        this.previewOpen = false
        this.selected = null
      }
    },
    goToRoot() {
      this.browseMode = false
      this.showRootView = true
      this.error = null
      this.files = []
      this.selected = null
      this.previewOpen = false
      useAppStore().navigateToRoute(FILE_MANAGER_ROOT_ROUTE, { replace: false })
    },
    async navigateToPath(path: string, options: { replace?: boolean } = {}) {
      try {
        this.showRootView = false
        let route: string
        if (this.browseMode) {
          route = serverPathToBrowseRoute(path)
        } else {
          route = serverPathToFileManagerRoute(path, this.fileRoots)
        }
        useAppStore().navigateToRoute(route, options)
        await this.load(path, { updateHistory: false })
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Could not open this folder'
      }
    },
    async navigateToBrowse(path = '/') {
      this.browseMode = true
      this.showRootView = false
      this.error = null
      const route = serverPathToBrowseRoute(path)
      useAppStore().navigateToRoute(route)
      await this.load(path, { updateHistory: false })
    },
    async openFolder(file: FileEntry) {
      await this.navigateToPath(file.path)
    },
    async search(q: string) {
      this.searchQuery = q
      if (!q.trim()) {
        this.searchResults = []
        this.searchError = null
        return
      }
      if (this.searchScope === 'current') {
        const needle = q.trim().toLocaleLowerCase()
        this.searchResults = this.files.filter((file) => file.name.toLocaleLowerCase().includes(needle))
        this.searchError = null
        return
      }
      searchController?.abort()
      searchController = new AbortController()
      const controller = searchController
      try {
        const data = await searchFiles(q.trim(), this.filterType === 'all' ? 'all' : this.filterType, controller.signal)
        this.searchResults = (data.results || []).map((f) => ({ ...f, kind: inferFileKind(f) }))
        this.searchError = null
      } catch (err) {
        if (controller.signal.aborted) return
        this.searchError = err instanceof Error ? err.message : 'Search failed'
        useAppStore().recordApiError(err as ApiError)
      }
    },
    selectFile(file: FileEntry | null) {
      this.selected = file
      this.previewOpen = !!file
    },
    previewFile(file: FileEntry, options: { replace?: boolean } = {}) {
      if (this.browseMode) {
        const route = serverPathToBrowseRoute(file.path)
        useAppStore().navigateToRoute(`${route}?preview=1`, options)
      } else {
        const route = serverPathToFileManagerRoute(file.path, this.fileRoots)
        useAppStore().navigateToRoute(`${route}?preview=1`, options)
      }
      this.selectFile(file)
    },
    closePreview() {
      this.previewOpen = false
      this.selected = null
      const route = this.browseMode
        ? serverPathToBrowseRoute(this.currentPath)
        : serverPathToFileManagerRoute(this.currentPath, this.fileRoots)
      useAppStore().navigateToRoute(route, { replace: true })
    },
    appUrl(file: FileEntry) {
      if (this.browseMode) {
        const route = serverPathToBrowseRoute(file.path)
        const url = new URL(route, window.location.origin)
        if (inferFileKind(file) !== 'folder') url.searchParams.set('preview', '1')
        return url.toString()
      }
      return appUrlForServerPath(file.path, this.fileRoots, inferFileKind(file) !== 'folder')
    },
    isStarred(file: FileEntry) {
      return this.files.find((item) => item.path === file.path)?.starred ?? file.starred ?? false
    },
    async copyCurrentFolderUrl() {
      const url = this.browseMode
        ? new URL(serverPathToBrowseRoute(this.currentPath), window.location.origin).toString()
        : appUrlForServerPath(this.currentPath, this.fileRoots)
      try {
        await copyToClipboard(url)
        useAppStore().toast('Folder URL copied', 'success')
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Clipboard access failed'
        useAppStore().logDiagnostic('clipboard', 'Could not copy folder URL', { url, message })
        useAppStore().toast(`Could not copy folder URL: ${message}`, 'error')
      }
    },
    async toggleStar(file: FileEntry) {
      const previous = !!this.isStarred(file)
      const setLocalStar = (value: boolean) => {
        file.starred = value
        for (const list of [this.files, this.searchResults]) {
          const match = list.find((item) => item.path === file.path)
          if (match) match.starred = value
        }
        if (this.selected?.path === file.path) this.selected.starred = value
      }
      setLocalStar(!previous)
      try {
        if (previous) await unstarFile(file.path)
        else await starFile(file.path, inferFileKind(file))
        await this.load(undefined, { updateHistory: false })
        useAppStore().toast(previous ? 'Removed from Starred' : 'Added to Starred', 'success')
      } catch (err) {
        setLocalStar(previous)
        const message = err instanceof Error ? err.message : 'Star update failed'
        useAppStore().logDiagnostic('starred', 'Could not update star', { path: file.path, message })
        useAppStore().toast(`Star was not changed: ${message}`, 'error')
      }
    },
    async createFolder(name: string) {
      await mkdir(this.currentPath, name)
      await this.load()
    },
    openCastPanel(file?: FileEntry) {
      if (file) this.selected = file
      this.castPanelOpen = true
    },
    setSort(sort: SortKey) {
      this.sort = sort
      localStorage.setItem('cm_sort', sort)
    },
    setViewMode(mode: ViewMode) {
      this.viewMode = mode
      localStorage.setItem('cm_view_mode', mode)
    },
    setFilterType(type: FileKind | 'all') {
      this.filterType = type
      localStorage.setItem('cm_filter_type', type)
    },
    setSearchScope(scope: 'current' | 'global') {
      this.searchScope = scope
      this.search(this.searchQuery)
    },
  },
})

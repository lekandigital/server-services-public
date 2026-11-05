import { defineStore } from 'pinia'
import { nanoid } from '../utils/id'
import type { DiagnosticEntry, NavSection, ToastItem } from '../types'
import type { ApiError } from '../api/client'

const SECTION_ROUTES: Record<NavSection, string> = {
  dashboard: '/dashboard',
  drive: '/drive',
  library: '/file-manager/root',
  recent: '/recent',
  starred: '/starred',
  shared: '/shared',
  torrents: '/torrents',
  queue: '/queue',
  playlists: '/playlists',
  storage: '/storage',
  trash: '/trash',
  activity: '/activity',
  settings: '/settings',
  diagnostics: '/diagnostics',
}

function sectionForPath(pathname: string): NavSection {
  if (pathname === '/' || pathname === '/dashboard') return 'dashboard'
  if (pathname === '/file-manager' || pathname === '/file-manager/root' || pathname.startsWith('/file-manager/')) return 'library'
  if (pathname === '/browse' || pathname === '/browse/' || pathname.startsWith('/browse/')) return 'library'
  const match = (Object.entries(SECTION_ROUTES) as Array<[NavSection, string]>).find(([, route]) => route === pathname)
  return match?.[0] || 'dashboard'
}

export const useAppStore = defineStore('app', {
  state: () => ({
    section: sectionForPath(window.location.pathname),
    sidebarCollapsed: localStorage.getItem('cm_sidebar_collapsed') === '1',
    diagnosticsOpen: false,
    toasts: [] as ToastItem[],
    diagnostics: [] as DiagnosticEntry[],
    lastApiError: null as ApiError | null,
    globalLoading: false,
    mobileNavOpen: false,
    serverOnline: true,
    sudoPrompt: null as {
      title: string
      message: string
      resolve: (password: string | null) => void
    } | null,
    sudoPromptError: '' as string,
  }),
  actions: {
    setSection(section: NavSection, options: { replace?: boolean } = {}) {
      this.section = section
      localStorage.setItem('cm_section', section)
      this.mobileNavOpen = false
      const route = SECTION_ROUTES[section]
      if (`${window.location.pathname}${window.location.search}` !== route) {
        window.history[options.replace ? 'replaceState' : 'pushState']({}, '', route)
      }
    },
    syncFromLocation() {
      this.section = sectionForPath(window.location.pathname)
      localStorage.setItem('cm_section', this.section)
      this.mobileNavOpen = false
    },
    navigateToRoute(route: string, options: { replace?: boolean } = {}) {
      window.history[options.replace ? 'replaceState' : 'pushState']({}, '', route)
      this.syncFromLocation()
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      localStorage.setItem('cm_sidebar_collapsed', this.sidebarCollapsed ? '1' : '0')
    },
    openDiagnostics() {
      this.diagnosticsOpen = true
    },
    closeDiagnostics() {
      this.diagnosticsOpen = false
    },
    toast(message: string, type: ToastItem['type'] = 'info', priority: ToastItem['priority'] = 'normal') {
      const item: ToastItem = { id: nanoid(), message, type, priority }
      this.toasts.push(item)
      if (this.toasts.length > 6) this.toasts.shift()
      setTimeout(() => this.dismissToast(item.id), priority === 'low' ? 2500 : 4500)
    },
    dismissToast(id: string) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
    logDiagnostic(category: string, message: string, details?: unknown) {
      this.diagnostics.unshift({ id: nanoid(), ts: Date.now(), category, message, details })
      if (this.diagnostics.length > 200) this.diagnostics.length = 200
    },
    recordApiError(err: ApiError) {
      this.lastApiError = err
      this.serverOnline = err.status !== 0
      this.logDiagnostic('api', err.message, { status: err.status, method: err.method, url: err.url, body: err.body })
    },
    clearDiagnostics() {
      this.diagnostics = []
      this.lastApiError = null
    },
    requestSudoPassword(options: { title?: string; message?: string } = {}): Promise<string | null> {
      // If a prompt is already open, cancel it before opening a new one.
      if (this.sudoPrompt) this.sudoPrompt.resolve(null)
      this.sudoPromptError = ''
      return new Promise<string | null>((resolve) => {
        this.sudoPrompt = {
          title: options.title || 'Sudo password required',
          message: options.message || 'This action needs elevated permissions on the server. Enter the sudo password to continue.',
          resolve,
        }
      })
    },
    submitSudoPassword(password: string) {
      const prompt = this.sudoPrompt
      this.sudoPrompt = null
      this.sudoPromptError = ''
      prompt?.resolve(password)
    },
    cancelSudoPassword() {
      const prompt = this.sudoPrompt
      this.sudoPrompt = null
      this.sudoPromptError = ''
      prompt?.resolve(null)
    },
  },
})

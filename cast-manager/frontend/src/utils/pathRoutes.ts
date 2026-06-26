import type { FileRoot } from '../types'

export const FILE_MANAGER_BASE = '/file-manager'
export const FILE_MANAGER_ROOT_ROUTE = '/file-manager/root'
export const FILE_MANAGER_ROOT_SENTINEL = '__FILE_MANAGER_ROOT__'

export const BROWSE_BASE_ROUTE = '/browse'
export const BROWSE_ROOT_ROUTE = '/browse/'

export function encodePathSegment(segment: string): string {
  return encodeURIComponent(segment)
}

export function decodePathSegment(segment: string): string {
  const decoded = decodeURIComponent(segment)
  if (!decoded || decoded === '.' || decoded === '..' || /[\\/\0]/.test(decoded)) {
    throw new Error('The file-manager URL contains an invalid path segment')
  }
  return decoded
}

export function normalizeServerPath(input: string): string {
  const value = String(input || '').replace(/\0/g, '').trim()
  if (!value.startsWith('/')) throw new Error('Server paths must be absolute')
  const parts = value.split('/').filter(Boolean)
  if (parts.some((part) => part === '.' || part === '..')) {
    throw new Error('Path traversal segments are not allowed')
  }
  return parts.length ? `/${parts.join('/')}` : '/'
}

function normalizeRoutePath(input: string): string {
  const value = `/${String(input || '').split('?')[0].split('#')[0].split('/').filter(Boolean).join('/')}`
  return value === '/' ? '/' : value.replace(/\/+$/, '')
}

function rootContainsPath(root: FileRoot, serverPath: string): boolean {
  const base = normalizeServerPath(root.serverPath)
  return serverPath === base || serverPath.startsWith(`${base}/`)
}

export function isPathInsideAllowedRoot(serverPath: string, roots: FileRoot[]): boolean {
  try {
    const normalized = normalizeServerPath(serverPath)
    return roots.some((root) => rootContainsPath(root, normalized))
  } catch {
    return false
  }
}

export function serverPathToFileManagerRoute(serverPath: string, roots: FileRoot[]): string {
  const normalized = normalizeServerPath(serverPath)
  const root = [...roots]
    .filter((candidate) => rootContainsPath(candidate, normalized))
    .sort((a, b) => b.serverPath.length - a.serverPath.length)[0]
  if (!root) throw new Error('This path is outside the configured file roots')
  const rootPath = normalizeServerPath(root.serverPath)
  const relative = normalized === rootPath ? '' : normalized.slice(rootPath.length + 1)
  const suffix = relative.split('/').filter(Boolean).map(encodePathSegment).join('/')
  const prefix = normalizeRoutePath(root.routePrefix)
  return suffix ? `${prefix}/${suffix}` : prefix
}

export function fileManagerRouteToServerPath(routePath: string, roots: FileRoot[]): string {
  if (!roots.length) throw new Error('No file roots are configured')
  const route = normalizeRoutePath(routePath)
  if (route === FILE_MANAGER_ROOT_ROUTE) return FILE_MANAGER_ROOT_SENTINEL
  if (route === FILE_MANAGER_BASE) return FILE_MANAGER_ROOT_SENTINEL
  const root = [...roots]
    .filter((candidate) => {
      const prefix = normalizeRoutePath(candidate.routePrefix)
      return route === prefix || route.startsWith(`${prefix}/`)
    })
    .sort((a, b) => b.routePrefix.length - a.routePrefix.length)[0]
  if (!root) throw new Error('This file-manager URL does not match a configured root')
  const prefix = normalizeRoutePath(root.routePrefix)
  const encodedRelative = route === prefix ? '' : route.slice(prefix.length + 1)
  const relative = encodedRelative.split('/').filter(Boolean).map(decodePathSegment).join('/')
  const serverPath = relative ? `${normalizeServerPath(root.serverPath)}/${relative}` : normalizeServerPath(root.serverPath)
  if (!isPathInsideAllowedRoot(serverPath, roots)) throw new Error('This route resolves outside the configured file roots')
  return serverPath
}

export function isBrowseRoute(routePath: string): boolean {
  const clean = String(routePath || '').split('?')[0].split('#')[0]
  return clean === '/browse' || clean === '/browse/' || clean.startsWith('/browse/')
}

export function browseRouteToServerPath(routePath: string): string {
  const clean = String(routePath || '').split('?')[0].split('#')[0]
  let rest: string
  if (clean === '/browse' || clean === '/browse/') {
    rest = ''
  } else if (clean.startsWith('/browse/')) {
    rest = clean.slice('/browse'.length)
  } else {
    throw new Error('Not a browse route')
  }
  const segments = rest.split('/').filter(Boolean)
  const decoded = segments.map((seg) => {
    const d = decodeURIComponent(seg)
    if (!d || d === '.' || d === '..' || d.includes('\0') || d.includes('/') || d.includes('\\')) {
      throw new Error(`Invalid path segment in browse URL: "${seg}"`)
    }
    return d
  })
  return decoded.length ? `/${decoded.join('/')}` : '/'
}

export function serverPathToBrowseRoute(serverPath: string): string {
  const normalized = normalizeServerPath(serverPath)
  if (normalized === '/') return '/browse/'
  const segments = normalized.split('/').filter(Boolean)
  return `${BROWSE_BASE_ROUTE}/${segments.map(encodePathSegment).join('/')}`
}

export function appUrlForServerPath(serverPath: string, roots: FileRoot[], preview = false): string {
  const route = serverPathToFileManagerRoute(serverPath, roots)
  const url = new URL(route, window.location.origin)
  if (preview) url.searchParams.set('preview', '1')
  return url.toString()
}

import type { CastStatus, CastUiState } from '../types'

const ACTIVE_STATES = new Set([
  'playing', 'paused', 'buffering', 'seeking', 'starting',
  'waiting_for_receiver_request', 'restarting_stream',
])

export function mapCastUiState(status: CastStatus | null, recentCommandAt = 0): CastUiState {
  if (!status) return 'idle'
  const raw = String(status.state || '').toLowerCase()
  const sessionActive = !!status.activeSession
  const recentCmd = recentCommandAt && Date.now() - recentCommandAt < 8000

  if (status.seekInProgress) return 'seeking'
  if (status.starting) return 'starting'
  if (status.restarting) return 'restarting_stream'

  if (raw.includes('error') || status.error) return 'error'
  if (raw === 'ended' || raw === 'stopped') return sessionActive ? 'stopping' : 'ended'
  if (raw === 'idle' && (sessionActive || recentCmd)) {
    if (status.lastCommandAt && Date.now() - status.lastCommandAt < 8000) return 'buffering'
    return sessionActive ? 'buffering' : 'idle'
  }
  if (raw.includes('wait')) return 'waiting_for_receiver_request'
  if (raw.includes('buffer')) return 'buffering'
  if (raw === 'paused') return 'paused'
  if (raw === 'playing') return 'playing'
  if (raw === 'seeking') return 'seeking'
  if (raw === 'starting') return 'starting'
  if (sessionActive) return 'playing'
  return 'idle'
}

export function shouldShowNowPlaying(ui: CastUiState, sessionActive: boolean, recentCommandAt: number): boolean {
  if (sessionActive) return true
  if (ACTIVE_STATES.has(ui) && ui !== 'idle' && ui !== 'ended') return true
  if (recentCommandAt && Date.now() - recentCommandAt < 12000) return true
  return false
}

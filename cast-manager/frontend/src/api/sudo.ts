import { ApiError } from './client'
import { useAppStore } from '../stores/appStore'

/** True when the server rejected an operation because it needs elevated (sudo) permissions. */
export function isPermissionDenied(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.status !== 403) return false
  const code = (err.body as { errorDetails?: { code?: string } } | undefined)?.errorDetails?.code
  return code === 'PERMISSION_DENIED'
}

/**
 * Runs a file operation that may require sudo. If the server responds with
 * PERMISSION_DENIED, the user is prompted for the sudo password and the action
 * is retried with it. The password is never cached — each escalation prompts again.
 */
export async function withSudoRetry<T>(
  action: (sudoPwd?: string) => Promise<T>,
  prompt: { title?: string; message?: string } = {},
): Promise<T> {
  try {
    return await action()
  } catch (err) {
    if (!isPermissionDenied(err)) throw err

    const app = useAppStore()

    // Allow re-entry on a wrong password until the user succeeds or cancels.
    for (;;) {
      const password = await app.requestSudoPassword(prompt)
      if (!password) throw err // user cancelled — surface the original error
      try {
        return await action(password)
      } catch (retryErr) {
        if (isPermissionDenied(retryErr)) {
          app.sudoPromptError = 'Incorrect password or insufficient permissions. Try again.'
          continue
        }
        throw retryErr
      }
    }
  }
}

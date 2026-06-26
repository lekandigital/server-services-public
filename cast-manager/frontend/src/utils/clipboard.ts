export async function copyToClipboard(text: string): Promise<void> {
  let clipboardError: unknown = null
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch (err) {
      clipboardError = err
    }
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)
  try {
    if (!document.execCommand('copy')) throw clipboardError || new Error('The browser rejected the copy command')
  } finally {
    textarea.remove()
  }
}

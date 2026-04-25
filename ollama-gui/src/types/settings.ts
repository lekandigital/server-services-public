export type ThemeMode = 'dark' | 'light'

export type ThemePreset =
  | 'default-dark'
  | 'default-light'
  | 'hacker'
  | 'paper'
  | 'high-contrast'

export interface AppSettings {
  theme: ThemeMode
  themePreset: ThemePreset
  enableMarkdown: boolean
  showSystemMessages: boolean
  historyLength: number
  baseUrl: string
  autoTitle: boolean
  showMetrics: boolean
  showTokenCounter: boolean
  sendOnEnter: boolean
}

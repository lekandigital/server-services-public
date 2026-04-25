# Architecture Decision Record

## Framework: Vue 3 (staying)

### Decision
Stay on Vue 3 with a modernized stack. No migration to React.

### Rationale

1. **Existing investment**: The current codebase has ~25 Vue SFCs with composables, reactive state, and `<script setup>` patterns. All server customizations (model rankings, streaming fixes, config migration) are Vue-native. Rewriting in React discards working, tested code for zero functional gain.

2. **Ecosystem fit**: Vue 3's ecosystem covers every requirement:
   - **Pinia** replaces the ad-hoc composables-as-global-state pattern with proper stores, devtools, and SSR-readiness
   - **VueUse** (already a dependency) provides 200+ composables for gestures, media queries, clipboard, intersection observers
   - **Radix Vue** provides unstyled, accessible UI primitives (dialogs, dropdowns, tooltips, command palette)
   - **Vue's `<Transition>`** handles animations without a separate library

3. **Bundle size**: Vue 3.5 core is ~33KB gzipped vs React 18 + ReactDOM at ~42KB. For a local tool, smaller = faster cold start.

4. **Solo developer ergonomics**: Vue SFCs co-locate template, logic, and scoped styles in a single file. This reduces context-switching and makes the codebase navigable without a mental map of separate JSX, hook, and CSS files.

5. **TypeScript**: Vue 3.5 has first-class TS support with `defineProps<T>()`, typed emits, typed slots, and generic components. No compromise vs React+TS.

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Framework | Vue 3.5+ | See above |
| Build | Vite 6 | Fast HMR, native ESM, proven with Vue |
| Language | TypeScript (strict) | Catch errors at compile time |
| State | Pinia | Official Vue state management, devtools, plugins |
| Styling | Tailwind CSS v3 | Utility-first, custom design tokens, purge unused |
| Database | Dexie.js 4 | IndexedDB wrapper, migration support, preserves v1 data |
| UI Primitives | Radix Vue | Unstyled accessible components (dialog, dropdown, tooltip, etc.) |
| Utilities | VueUse | Composables for browser APIs, gestures, sensors |
| Icons | Tabler Icons Vue | Already in use, comprehensive set |
| Markdown | markdown-it + Shiki | GFM support + modern syntax highlighting |
| Math | KaTeX | LaTeX rendering for math equations |
| Diagrams | Mermaid | Diagram rendering in code blocks |
| HTTP | Native fetch | No axios needed for simple REST + streaming |
| Animations | Vue Transition + CSS | No extra library needed |

## Project Structure

```
src/
├── main.ts                    # App entry
├── App.vue                    # Root layout
├── router.ts                  # Vue Router (if needed for future pages)
│
├── assets/
│   └── fonts/                 # Self-hosted Inter + JetBrains Mono
│
├── design/
│   ├── tokens.css             # CSS custom properties (colors, spacing, typography)
│   ├── base.css               # Reset, global styles
│   └── tailwind.css           # Tailwind directives
│
├── components/
│   ├── ui/                    # Design system primitives
│   │   ├── Button.vue
│   │   ├── Input.vue
│   │   ├── Textarea.vue
│   │   ├── Modal.vue
│   │   ├── Drawer.vue
│   │   ├── Dropdown.vue
│   │   ├── Toast.vue
│   │   ├── Tooltip.vue
│   │   ├── Skeleton.vue
│   │   ├── Toggle.vue
│   │   ├── Tabs.vue
│   │   └── CommandPalette.vue
│   │
│   ├── chat/                  # Chat-specific components
│   │   ├── ChatView.vue       # Main chat area
│   │   ├── ChatInput.vue      # Message input with auto-resize
│   │   ├── ChatMessages.vue   # Scrollable message list
│   │   ├── MessageBubble.vue  # Single message (dispatches to role-specific)
│   │   ├── UserMessage.vue
│   │   ├── AiMessage.vue
│   │   ├── SystemMessage.vue
│   │   ├── ThinkBlock.vue     # Collapsible <think> content
│   │   ├── CodeBlock.vue      # Syntax highlighted code with copy
│   │   ├── StreamingIndicator.vue
│   │   └── ResponseMetrics.vue
│   │
│   ├── sidebar/               # Left sidebar
│   │   ├── Sidebar.vue
│   │   ├── ChatList.vue
│   │   ├── ChatListItem.vue
│   │   ├── FolderTree.vue
│   │   └── SearchBar.vue
│   │
│   ├── settings/              # Settings panels
│   │   ├── SettingsPanel.vue
│   │   ├── ModelSelector.vue
│   │   ├── SystemPrompt.vue
│   │   ├── AppearanceSettings.vue
│   │   └── ConnectionStatus.vue
│   │
│   ├── models/                # Model management
│   │   ├── ModelManager.vue
│   │   ├── ModelCard.vue
│   │   └── ModelPullProgress.vue
│   │
│   └── markdown/              # Markdown rendering
│       ├── Markdown.vue
│       └── plugins/           # markdown-it plugins
│
├── stores/                    # Pinia stores
│   ├── chatStore.ts           # Chat CRUD, active chat, messages
│   ├── modelStore.ts          # Available models, current model, rankings
│   ├── settingsStore.ts       # User preferences, appearance
│   ├── connectionStore.ts     # Ollama connection status
│   └── uiStore.ts             # Sidebar state, modals, panels
│
├── services/                  # Non-reactive services
│   ├── ollama.ts              # Ollama API client (fetch + streaming)
│   ├── database.ts            # Dexie schema + migrations
│   └── search.ts              # Full-text search across chats
│
├── composables/               # Reusable composition functions
│   ├── useStreaming.ts         # Stream management + abort
│   ├── useKeyboardShortcuts.ts
│   ├── useAutoScroll.ts       # Smart scroll during streaming
│   ├── useTokenCounter.ts     # Live token estimation
│   └── useAutoTitle.ts        # Background title generation
│
└── types/                     # TypeScript types
    ├── chat.ts
    ├── ollama.ts
    └── settings.ts
```

## Data Flow

```
User Input → ChatInput.vue
  → chatStore.sendMessage()
    → ollama.chat() (streaming fetch)
      → onToken callback → chatStore.appendToken()
        → reactive update → AiMessage.vue re-renders
      → onComplete → chatStore.finalizeMessage()
        → database.messages.put()
        → useAutoTitle() triggers if first exchange
```

## Database Schema (Dexie)

Migration-compatible with v1 (existing IndexedDB data preserved):

```typescript
// v10 (existing) — preserved
chats:    ++id, name, model, createdAt
messages: ++id, chatId, role, content, meta, context, createdAt
config:   ++id, model, systemPrompt, createdAt

// v11 (new fields, additive only)
chats:    ++id, name, model, createdAt, folderId, pinned, archived, tags, lastMessageAt
messages: ++id, chatId, role, content, meta, context, createdAt, parentId, bookmarked, branchId
folders:  ++id, name, parentId, order, createdAt
prompts:  ++id, title, content, category, createdAt
snippets: ++id, title, content, tags, messageId, createdAt
```

## API Patterns

- All Ollama calls go through `src/services/ollama.ts`
- Streaming uses `ReadableStream` with newline-delimited JSON parsing (preserving the buffer fix from v1)
- Every request accepts an `AbortSignal` for cancellation
- Connection status is tracked globally via `connectionStore`
- Retry logic: exponential backoff for transient failures, immediate fail for 4xx

## Key Decisions

1. **No Vue Router initially** — Single-page app, all navigation via component visibility. Router can be added later if pages are needed.
2. **CSS custom properties for theming** — Tailwind references CSS vars, enabling runtime theme switching without rebuilding.
3. **Lazy loading** — Heavy features (Mermaid, KaTeX, model management, statistics) loaded via dynamic `import()`.
4. **Mobile-first** — Base styles target mobile, `md:` and `lg:` breakpoints add desktop enhancements.
5. **No SSR** — This is a local tool. Client-only rendering is fine.

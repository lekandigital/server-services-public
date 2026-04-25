# CLAUDE.md -- Developer Guide for AI Assistants

This is an Ollama chat GUI built with Vue 3.5, Vite 6, Pinia, Tailwind CSS, and TypeScript in strict mode.

## Project Structure

```
src/
  App.vue                  -- Root layout, wires together sidebar + chat area
  main.ts                  -- App entry point, registers Pinia and global styles
  types/
    chat.ts                -- Chat, Message, Bookmark types
    ollama.ts              -- Ollama API request/response types
    settings.ts            -- Settings and theme types
  services/
    database.ts            -- Dexie (IndexedDB) schema and database instance
    ollama.ts              -- Ollama HTTP client: list models, chat, pull, delete, streaming
  stores/
    chatStore.ts           -- Chat CRUD, message management, active chat state
    connectionStore.ts     -- Ollama server connection status
    modelStore.ts          -- Model list, pull/delete, star rankings
    settingsStore.ts       -- Theme, preferences, import/export
    uiStore.ts             -- Sidebar visibility, modals, transient UI state
  components/
    chat/                  -- Message list, input area, streaming display, think blocks
    sidebar/               -- Chat list, search, navigation
    settings/              -- Settings panel, theme picker
    models/                -- Model manager, pull UI, star rankings
    markdown/              -- Markdown renderer, code blocks with syntax highlighting
    ui/                    -- Shared primitives (buttons, modals, dropdowns, badges)
  composables/
    useAutoTitle.ts        -- Auto-generates chat titles from first message
    useKeyboardShortcuts.ts -- Global keyboard shortcut bindings
  design/
    base.css               -- Reset and global base styles
    tailwind.css           -- Tailwind directives (@tailwind base/components/utilities)
    tokens.css             -- CSS custom properties for theming
```

## Key Patterns

**State management:** All application state flows through Pinia stores. Components read from stores via `storeToRefs()` and dispatch actions. Never mutate store state directly from components.

**Theming:** Five theme presets implemented via CSS custom properties defined in `src/design/tokens.css`. The active theme class is applied to the root element. Tailwind classes reference these tokens.

**Custom design tokens:** Tailwind is extended with semantic color tokens:
- `surface-0` through `surface-3` -- Background layers (0 = deepest, 3 = most elevated)
- `accent` -- Primary action color
- `text-primary`, `text-secondary`, `text-muted` -- Text hierarchy

**Streaming:** Chat responses stream via async generators in `src/services/ollama.ts`. The chat store consumes the generator and appends tokens to the active message reactively.

**Database:** Dexie wraps IndexedDB for persistent storage of chats, messages, and settings. The database schema is in `src/services/database.ts`.

**Path aliases:** `@` maps to `src/` (configured in `vite.config.ts`).

## Commands

```bash
npm run dev        # Start dev server on port 8081
npm run build      # Type-check then build for production
npm run typecheck  # Run vue-tsc --noEmit
npm run format     # Prettier with Tailwind plugin
npm run preview    # Preview production build on port 8081
```

## How to Add a New Feature

1. **Define types** in `src/types/` if the feature introduces new data shapes.
2. **Create or extend a Pinia store** in `src/stores/` for any new state or logic.
3. **Build the component** in the appropriate `src/components/` subdirectory.
4. **Wire it into the layout** -- either add it to `App.vue` or nest it within an existing component tree.
5. **Add keyboard shortcuts** in `src/composables/useKeyboardShortcuts.ts` if applicable.
6. **Update the database schema** in `src/services/database.ts` if persisting new data.

## Code Conventions

- TypeScript strict mode is enabled. Do not use `any`.
- Use Vue 3 `<script setup lang="ts">` for all components.
- Use Composition API exclusively; no Options API.
- Tailwind utility classes for styling; avoid inline styles.
- Imports use the `@/` path alias (e.g., `import { useChatStore } from '@/stores/chatStore'`).

## Docker

- `Dockerfile` -- Multi-stage build: Node 20 Alpine for build, Nginx Alpine for serving.
- `compose.yml` -- Runs GUI (port 8081:80) and Ollama (port 11435:11434).
- `nginx/default.conf` -- Proxies `/api/` to the Ollama container.

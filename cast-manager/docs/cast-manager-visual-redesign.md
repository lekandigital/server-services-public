# Cast Manager Visual Redesign

## Direction

Cast Manager will use a light-mode-first media command-center layout: warm off-white canvas, white bordered surfaces, a calm blue accent, readable slate typography, clear semantic statuses, and a persistent high-confidence cast control surface.

## Acceptance principles

- Light is the default and authoritative theme.
- Navigation uses consistent icons plus labels; no emoji UI.
- Library content has the strongest hierarchy and generous scanning space.
- Buttons communicate action, state, loading, and disabled reason.
- Diagnostics remain available without turning the dashboard into a debug dump.
- Mobile keeps the same core workflows without cramped controls.

## Delivered system

- Warm off-white canvas with white bordered surfaces, restrained shadows, high-contrast slate typography, calm blue actions, and semantic green/amber/red states.
- Finder/iPadOS-inspired grouped sidebar with labeled SVG icons and a compact server/device status footer.
- Section-aware top bar with current context, refresh, device/cast status, and Diagnostics shortcut.
- Dashboard organized around Active Cast, analyze-first Quick Cast, Continue Watching, storage health, torrent health, and direct shortcuts.
- Library optimized for scanning: breadcrumbs, search, type filter, sort, list/grid, large targets, explicit per-type actions, and reliable error/empty/loading states.
- Media details drawer that never opens a knowingly incompatible black video player.
- Persistent Now Playing controller that survives startup, buffering, seeking, restart, and temporary polling states.
- Diagnostics designed as a support workspace rather than a debug dump.

## Responsive behavior

Desktop uses a persistent 250px sidebar and two-column dashboard. Below 820px, navigation becomes an off-canvas menu, cards stack, controls wrap, and Now Playing becomes a compact two-row floating controller. Evidence: `diagnostics/cast-manager-light-ui/mobile-light.png`.

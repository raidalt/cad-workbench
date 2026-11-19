# cad chat

The chat panel for the `cad` live viewport, embedded by the viewer as
`<iframe src="/static/chat/">`.

It is a Next.js App Router project with **static export**: `cad serve` serves
plain files from `src/cad/web/chat/`, the wheel ships them, and no Node is
needed at runtime.

## Rebuild

From this directory:

```sh
npm ci
npm run typecheck   # tsc --noEmit
npm run build
```

`npm run build` runs `next build` and then `scripts/sync-export.mjs`, which
replaces `../src/cad/web/chat/` with the contents of `out/`. That folder is the
committed artifact the Python package serves at `/static/chat/`.

Do not edit `../src/cad/web/chat/` by hand. Rebuild it here.

## Versions

| Package | Version |
| --- | --- |
| `next` | `16.4.6` (pinned exactly in `package.json`) |
| `react` / `react-dom` | `19.3.0` |
| `streamdown` | `2.7.1` |
| Tailwind CSS | `4.3.3` (Turbopack loader) |
| shadcn | `4.23.0`, style `base-rhea`, base `base` |

`npm ci` resolves `next@16.4.6` from the lockfile.

## Why this config

`next.config.ts`:

```ts
output: "export",
basePath: "/static/chat",
assetPrefix: "/static/chat/",
trailingSlash: true,
images: { unoptimized: true },
```

- `output: "export"` produces `out/` at build time; there is no server.
- `basePath` makes the page live at `/static/chat/` and prefixes the export.
- `assetPrefix` keeps every `_next/` URL under `/static/chat/` as well.
- `trailingSlash` writes `/index.html`, so the Python server's
  "serve `index.html` for a directory" rule finds it.
- `images.unoptimized` removes the image optimizer, which needs a server.

The browser calls the backend on the same origin:

- `fetch("/api/chat")`, `fetch("/api/chat/stop")`, `fetch("/api/chat/undo")`
- `new EventSource("/api/events")`

None of those go through Next. There are no route handlers, server actions,
`headers()`, `cookies()`, or other dynamic APIs, so the export stays static.

The only Next-specific pieces are `layout.tsx` metadata and the client
components. The page renders a `"use client"` `ChatApp`; every interactive app
file and every shadcn primitive that uses a hook is inside that client
boundary. The presentational primitives (`message`, `bubble`, `input-group`,
`trace`) are imported only from client components, so they never run on the
server.

## AI Elements used

Installed with the shadcn CLI:

```sh
npx shadcn@latest init -b base -p rhea
npx shadcn@latest add message-scroller message bubble input-group trace code-block button collapsible
```

| Pattern | Source | Where |
| --- | --- | --- |
| Chat / Message Scroller | https://ui.shadcn.com/docs/ai-elements/base/chat.md | `components/chat/thread.tsx`, `components/ui/message-scroller.tsx` |
| Message | https://ui.shadcn.com/docs/ai-elements/base/message.md | `components/chat/message-item.tsx`, `components/ui/message.tsx`, `components/ui/bubble.tsx` |
| Prompt Input | https://ui.shadcn.com/docs/ai-elements/base/prompt-input.md | `components/ai-elements/prompt-input.tsx`, `components/ui/input-group.tsx` |
| Reasoning | https://ui.shadcn.com/docs/ai-elements/base/reasoning.md | `components/ai-elements/reasoning.tsx`, `components/ui/trace.tsx` |
| Code Block | https://ui.shadcn.com/docs/components/base/code-block.md | `components/ai-elements/code-block.tsx`, `components/ui/code-block.tsx` |
| Suggestion | https://ui.shadcn.com/docs/ai-elements/base/suggestion.md | `components/ai-elements/suggestion.tsx`, `components/ui/button.tsx` |
| Trace | https://ui.shadcn.com/docs/components/base/trace.md | `components/ui/trace.tsx` |
| Response (Streamdown) | https://streamdown.ai | `components/ai-elements/response.tsx` |

### Substitutions and adaptations

- **Code Block highlighting.** The docs' highlighter is optional (`shiki`).
  This app only ever shows a `model.py` fence, so `python-highlight.tsx` is a
  small Python tokenizer loaded as its own dynamic chunk. Shiki would have
  added roughly a megabyte for one language. Copy still uses the raw source.
- **Response.** `streamdown` is used for completed replies only, with the
  fenced `python` block routed to the collapsed Code Block. Streamdown's own
  Shiki/mermaid plugins are not installed.
- **No attachment/dropdown menus.** The Prompt Input pattern supports them, but
  this chat only needs text, send, and stop, so those registry items were not
  added.
- **Fonts.** `next/font` was removed. The app uses the viewport's system mono
  stack, so no font request or `next/font` runtime is involved.
- **Theme.** `globals.css` carries the palette from `src/cad/web/style.css`
  (`#0b0d10`, `#111419`, `#222832`, `#7aa2d6`, ...) into the shadcn CSS
  variables. `html` has `class="dark"`; radii are 2 px.

## Performance shape

- The in-flight reply lives in `lib/stream-store.ts`, outside React state.
  `chat_delta` appends to a string and schedules **one** `requestAnimationFrame`
  flush. Only `StreamingMessage` subscribes (`useSyncExternalStore`).
- Streaming is plain text in a `<pre>`. Markdown (`Response`) and the Code
  Block render only after `chat_done`, when one message is committed. The
  thread list updates once for the user turn and once for the completed reply.
- Every committed `MessageItem` is `React.memo` with a stable key.
  `MessageScrollerItem` already applies `content-visibility: auto` with
  `contain-intrinsic-size`.
- The composer is an uncontrolled `<textarea>`; Enter sends, Shift+Enter adds a
  line, Escape blurs. It is disabled while a reply streams, and the Stop button
  stays live.
- Autoscroll runs in `requestAnimationFrame` and only within 80 px of the
  bottom.
- One `EventSource("/api/events")` for the page lifetime. `GET /api/chat` runs
  once on mount and again only after a reconnect.
- `chat_error` is terminal: the partial text/thinking is committed, the error
  is shown inline, and the composer is re-enabled. `chat_metrics` renders as a
  small dim line (`ttft 210 ms · 1.4 s · apply 4 ms`) when present.
- The Response chunk and the Python highlighter are separate lazy chunks. The
  Response chunk warms on the first composer focus/input and on `chat_start`,
  so first paint never pays for it; the highlighter loads only when a completed
  reply contains a python block.

## Turn outcomes, recovery, and ambiguity

These are committed with the message (never streamed), so they do not change
the one-commit-per-reply shape.

**Outcome strip.** Every assistant turn can carry `outcome`
(`applied, verified, revision, reverted, error, ms, build_ms`) from
`chat_done.outcome` or `history[].outcome`. The strip under the turn is derived
only from those fields:

| State | Strip |
| --- | --- |
| applied + verified | green `r6 · built in 321 ms · updated model.py` |
| applied, build failed, reverted | red `build failed — model.py left unchanged (still r5)` + error code/message |
| error / no text / connection | amber `the model returned no text` + error code, with Retry / Edit prompt |
| stopped | dim `stopped` |

An assistant turn with no `outcome` renders no strip: nothing is inferred from
the model's prose. `history[].outcome`/`failed` are hydrated by
`lib/history.ts`, so the strip and recovery controls survive a reload.

**Recoverable failed turns.** A failed assistant row keeps the original prompt
visible. `Retry` posts `{message, retry_turn}` with the user turn's server id;
the same user bubble stays in place (nothing is appended) and the failed reply
plus anything after it is dropped, matching the server, which replaces that
turn and everything after it. `Edit prompt` removes the failed reply, puts the
text back in the uncontrolled composer focused, and the next send updates the
same user bubble in place and carries the same `retry_turn`. No duplicate user
bubble and no second application of an earlier edit. Retry is disabled while a
reply streams.

**Clear-board ambiguity.** A message matching the clear/wipe/empty intent (or a
reply that proposes clearing) never reaches the model. An inline, keyboard
accessible card offers: **hide parts** (`window.parent.postMessage({type:
"cad:hide-parts"}, "*")`, then a local note; no API call and no model change),
**start a new model** (`POST /api/new-model`, then a note naming the new
revision and saying older revisions stay in the history selector), and
**cancel** (dismiss and restore the prompt to the composer). The card is
dismissed after a choice.

**Empty / zero-part revisions.** `GET /api/state` is read on mount and after
state/build/chat events. When `empty` is true the thread shows a quiet line
with a `new model` action. If the last verified chat revision differs from
`head`, it says `r5 is shown; r6 has no parts` instead of claiming the viewport
is empty; otherwise it says `r6 has no parts — start a model or ask for one`.

**Legibility.** Strips clamp to two lines with the full text in the `title`
tooltip, error text is selectable, and every control keeps a visible
`focus-visible` ring. Failed/reverted prose is collapsed under a
`proposed reply — not applied` disclosure so the authoritative result comes
first.

# Decisions

Architecture and product decisions for the portfolio chatbot. Newest first.
Each entry records the date, the decision, the alternatives considered, and the
reason. Add a new entry rather than rewriting an old one; if a decision is
reversed, record the reversal as its own entry.

## Template

```markdown
### YYYY-MM-DD - Title

- **Decision:** What we are doing.
- **Alternatives considered:** What we chose not to do.
- **Reason:** Why this option wins for this project.
```

### 2026-11-09 - Stateless server with browser-held chat history

- **Decision:** The server keeps no conversation state. The browser holds the
  transcript and sends the last eight messages with each `POST /api/chat`
  request. Nothing is persisted server-side.
- **Alternatives considered:** A server-side session store (Redis or a
  database); a signed cookie carrying the transcript; replaying the full chat
  history on every request.
- **Reason:** Visitors do not have accounts, so there is no natural key for
  server-side sessions. Statelessness avoids storing visitor text (no PII to
  retain or delete), survives free-tier restarts that wipe in-memory state, and
  keeps the prompt bounded in size and cost. The client already owns the
  transcript, so sending a window of it is the simplest correct design.

### 2026-11-09 - No vector database; two-tier knowledge in the prompt

- **Decision:** The knowledge base is `knowledge/core.json` (a compact tier-1
  profile, always loaded) plus Markdown deep dives in `knowledge/sections/`.
  A deterministic keyword router selects zero to two sections per request; no
  embeddings, vector database, or retrieval service.
- **Alternatives considered:** A vector database with retrieval-augmented
  generation (pgvector, Chroma, Pinecone); putting the whole knowledge base in
  every prompt; fine-tuning a model on the profile.
- **Reason:** The corpus is small, stable, and hand-curated. The core plus two
  sections fits comfortably in the context window, so retrieval would add
  infrastructure and latency without improving answers. Keyword routing is
  deterministic, free, and directly testable. Injecting the full knowledge base
  would be larger than necessary and would dilute the system instructions.

### 2026-11-09 - Server-Sent Events over WebSocket for chat streaming

- **Decision:** `POST /api/chat` responds with a `text/event-stream` body. The
  server streams text chunks for one request and then closes the stream.
- **Alternatives considered:** WebSocket; long polling; a single non-streaming
  JSON response.
- **Reason:** Each turn is one request followed by one response; the server
  never pushes unsolicited messages. SSE works over plain HTTP and Render's
  proxy, needs no handshake or reconnect library, and is simple to consume from
  vanilla JavaScript with `fetch` and `ReadableStream`. A WebSocket would add
  connection state and infrastructure for no product benefit.

### 2026-11-09 - Vanilla JS frontend served by FastAPI

- **Decision:** The UI is plain HTML, CSS, and JavaScript in `frontend/`, served
  as static files by the same FastAPI application. There is no build step.
- **Alternatives considered:** A React or Vite single-page app; Next.js; a
  server-side templating framework.
- **Reason:** The product is a single chat view, and one deploy target (Render)
  keeps operations simple. Serving the UI and API from the same origin removes
  CORS configuration, and avoiding a Node toolchain removes a build pipeline
  and its dependency surface without losing any capability the UI needs.

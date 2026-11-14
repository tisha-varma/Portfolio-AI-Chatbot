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

### 2026-11-14 - Right-most forwarded hop for client IPs

- **Decision:** Use the direct peer address by default. Only when
  `TRUSTED_PROXY=true` does the server read `X-Forwarded-For`, and it takes the
  right-most valid hop (the address appended by the trusted proxy closest to
  the app).
- **Alternatives considered:** Always trusting the left-most header value;
  trusting every hop; requiring a configured proxy CIDR list.
- **Reason:** A client can spoof any header it sends, so forwarded headers are
  ignored unless a trusted proxy is explicitly configured. With one trusted
  reverse proxy, the right-most hop is the address that proxy actually saw.
  Using the left-most value by default would let visitors bypass per-IP limits.

### 2026-11-14 - In-memory sliding windows for abuse protection

- **Decision:** Per-IP minute and day limits and the global daily cap are kept
  in process memory as sliding windows. The global counter resets by UTC date;
  per-IP buckets expire after their window and are swept every minute.
- **Alternatives considered:** A Redis or database-backed counter; fixed
  one-minute buckets; relying only on the hosting provider's limits.
- **Reason:** The service runs as a single Render instance, so in-memory state is
  sufficient and adds no network dependency or cost. Sliding windows avoid the
  burst-at-the-boundary problem of fixed buckets. A restart resets the counters,
  which is acceptable because the free Groq quota is the resource being
  protected, not billing state.

### 2026-11-14 - Strict CSP instead of inline frontend code

- **Decision:** Every response carries a Content-Security-Policy with
  `default-src 'self'`, `script-src 'self'`, and `style-src 'self'`. The
  frontend must ship external CSS and JavaScript files and no inline handlers.
- **Alternatives considered:** Allowing `'unsafe-inline'`; a per-response nonce.
- **Reason:** The frontend is fully controlled and static, so it can meet the
  strict policy without a build step. Avoiding inline code removes the most
  common cross-site scripting sink. A nonce would add template rewriting for no
  benefit when no inline code is needed.

### 2026-11-14 - JSON logs carry metadata, never visitor text

- **Decision:** Application logs are one JSON object per line and include the
  request id, route, status, duration, message count, and error type. Message
  content is never logged.
- **Alternatives considered:** Plain-text logs; logging full prompts for
  debugging; relying on the hosting provider's request logs only.
- **Reason:** Prompts can contain personal or confidential text from visitors
  (for example, a pasted job description). Metadata is enough to diagnose
  latency and failures without retaining content. Error details stay in
  server-side logs while visitors receive a generic message.


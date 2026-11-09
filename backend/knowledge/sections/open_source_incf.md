# Open Source Contribution - INCF Knowledge-Space AI Agent

Dec 2025 - Mar 2026, remote.

INCF is the International Neuroinformatics Coordinating Facility. The Knowledge-Space AI Agent is a neuroscience dataset-discovery agent (Python, LangGraph backend, Gemini) used by researchers worldwide.

Upstream repository: https://github.com/INCF/knowledge-space-agent

Tisha contributed on her own initiative, following the issue -> branch -> pull request -> code review -> fix workflow inside an existing Python/LLM codebase, and replied to reviewers and other contributors in the pull-request threads.

## PR #30 - MERGED

"Fix empty retrieval and API error handling in chat responses" (fixes issue #15)

https://github.com/INCF/knowledge-space-agent/pull/30

- Returns a clear message when retrieval finds no results.
- Wraps the LLM/API call so failures give a friendly fallback.
- Stops internal error details from reaching users.
- Also included the missing "cd backend" README step.
- The maintainer's review noted it adds an empty-result guard, wraps the Gemini call in try/except and removes internal error details, and then merged it.

## PR #90 - APPROVED BY MAINTAINERS

"Add MAX_QUERY_LENGTH guard in handle_chat to prevent overly long queries" (fixes issue #83)

https://github.com/INCF/knowledge-space-agent/pull/90

- Configurable MAX_QUERY_LENGTH environment variable (default 2000 characters), validated early in handle_chat after trimming whitespace; tests added; documented in .env.template.
- Prevents oversized inputs reaching the Gemini API, which avoids token-limit errors, application-layer denial-of-service risk and wasted API quota.
- After a reviewer noted that int(os.getenv(...)) would raise an error on a non-integer value, she added a try/except fallback to the default.

## PR #44 - OPEN (as of 6 Nov 2026)

"feat: add minimal end-to-end example for running the agent locally" (fixes issue #35, which she opened herself)

https://github.com/INCF/knowledge-space-agent/pull/44

- Adds examples/README.md, examples/basic_demo.py and examples/local_knowledge.json (illustrative mock data).
- The demo needs only GOOGLE_API_KEY and Python 3.11+.
- Handles unconfigured search services (the agent returns zero results gracefully).
- Another contributor said he would test it locally.

## PR #61 - OPEN (as of 6 Nov 2026)

"feat: add pluggable chat storage with optional SQLite persistence and session-scoped caching" (closes issue #60)

https://github.com/INCF/knowledge-space-agent/pull/61

- New backend/chat_storage.py: a ChatStorage abstraction with InMemoryChatStorage (default) and SQLiteChatStorage (opt-in, WAL mode, thread-safe through a lock and a persistent connection).
- New backend/response_cache.py: session-scoped cache with TTL expiry and LRU eviction; cache keys include the session_id to prevent cross-session leakage.
- NeuroscienceAssistant in backend/agents.py uses the pluggable storage and cache; the handle_chat signature is unchanged.
- Observability endpoints: GET /api/sessions, GET /api/cache/stats, POST /api/cache/clear.
- Environment options: ENABLE_PERSISTENCE, ENABLE_CACHE, CHAT_DB_PATH, CACHE_MAX_SIZE (128), CACHE_TTL_SECONDS (3600).
- Falls back to in-memory storage with a warning if SQLite fails.
- Standard library only (sqlite3, hashlib, OrderedDict, threading); 29 new unit tests (17 for storage, 12 for the cache), all passing.

## PR #29 - CLOSED (duplicate of #30)

"docs(README): fix backend run instructions" (fixes issue #22)

https://github.com/INCF/knowledge-space-agent/pull/29

- Adds the missing "cd backend" step before "uv run main.py".

Summary: 1 merged PR, 1 PR approved by maintainers, 2 open PRs and 1 closed duplicate.

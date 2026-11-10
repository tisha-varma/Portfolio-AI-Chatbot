# Portfolio AI Chatbot

A public chatbot where recruiters can ask about Tisha's projects, skills, CGPA,
and fit for a job description. The guiding rule is **honesty over polish**: the
bot may only state facts that exist in the knowledge base.

This phase adds a two-tier knowledge base and a keyword section router:

- `knowledge/core.json` - a compact tier-1 profile that is always in the prompt
- `knowledge/sections/*.md` - detailed deep dives loaded only when relevant
- `app/knowledge_router.py` - keyword scoring, no LLM, no extra API call
- `app/prompt_builder.py` - validates the core, injects it as compact JSON, and
  appends the selected deep dives
- `app/main.py` - a minimal `POST /chat` endpoint that routes, builds the
  prompt, and calls the configured Groq model

There is **no frontend, no streaming, no CORS configuration, and no JD matching
endpoint** yet.

## Folder layout

```text
portfolio-chatbot/
  backend/
    app/
      __init__.py             Package marker and version.
      config.py               Pydantic settings, cached get_settings().
      knowledge_router.py     Keyword section router (select_sections).
      main.py                 FastAPI app with GET /health and POST /chat.
      prompt_builder.py       Validates the core, renders the system prompt.
    prompts/
      system_prompt.md        Prompt template with {{OWNER_NAME}},
                              {{CONTACT_EMAIL}}, and {{KNOWLEDGE_BASE}}.
    knowledge/
      core.json               Tier 1: always-loaded profile facts.
      sections/
        index.json            Section titles, keywords, and descriptions.
        razorflow_ai.md       Tier 2: on-demand deep dives.
        career_copilot.md
        amazon_recommender.md
        pharmamate.md
        ai_study_buddy.md
        workchain.md
        open_source_incf.md
        other_projects.md
      source/
        tisha_varma_knowledge_base.txt   The only source of truth.
      TODO_FOR_TISHA.md       Ungrounded items to confirm; never loaded.
    tests/
      test_prompt_builder.py  Config and prompt-building tests.
      test_knowledge_base.py  Fidelity, size, and structure checks.
      test_knowledge_router.py  Router behavior.
      test_chat_endpoint.py   POST /chat wiring, with a fake Groq client.
      honesty_questions.md    Adversarial checklist with expected behavior.
    conftest.py               sys.path bootstrap and per-test cache clearing.
    requirements.txt
  frontend/                   Reserved for the chat UI. Empty for now.
  .env.example
  .gitignore
  README.md
```

## Setup

Requires Python 3.11 or newer.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
Copy-Item .env.example .env
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
cp .env.example .env
```

Then edit `portfolio-chatbot/.env` and set at least:

- `GROQ_API_KEYS` - one or more real keys from
  <https://console.groq.com/keys>, comma-separated. The legacy
  `GROQ_API_KEY` variable is still accepted as a fallback.
- `GROQ_MODEL` - the exact primary model id you want to use
- `CONTACT_EMAIL` - the public address visitors should use

The project `.env` is the only dotenv file loaded; its path is derived from
`config.py`, so it works from any working directory. Real environment
variables override it. The file is gitignored, and every key is stored as a
`SecretStr`, so keys render as `**********` in `repr()` and `str()`. Use
`settings_summary()` for logging: it reports the key count and model names,
never key text.

`get_settings()` raises a `RuntimeError` naming any missing variable, so a
misconfigured environment fails immediately instead of at request time.

## Run the tests

The tests do not call Groq and do not need a real API key. The chat test
replaces the Groq client with a fake.

```bash
cd backend
python -m pytest -q
```

Or from the project root:

```bash
python -m pytest backend -q
```

## Secret scanning

`scripts/check_secrets.sh` scans the staged index for Groq, Google, and OpenAI
style keys and private-key headers. It prints file names only, never the matched
text, and exits non-zero so the commit is blocked.

Install it as a pre-commit hook (Git Bash, macOS, or Linux):

```bash
cp scripts/check_secrets.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

Run it manually at any time:

```bash
bash scripts/check_secrets.sh
```

Hooks live in `.git/hooks`, which is not committed, so every clone runs the
install command once. If the script flags a real key, remove it from the staged
files and rotate it in the provider's console before retrying the commit.

## Run the API

```bash
cd backend
python -m uvicorn app.main:app --reload
```

`POST /chat` accepts a conversation and returns the reply plus the section ids
that were loaded:

```json
{
  "messages": [
    {"role": "user", "content": "tell me about RazorFlow payments"}
  ]
}
```

```json
{
  "reply": "...",
  "sections": ["razorflow_ai"]
}
```

`GET /health` returns `{"status": "ok"}`. `system` messages are rejected by the
request model so a visitor cannot inject their own instructions.

## The two-tier knowledge base

### Tier 1: `knowledge/core.json`

Always loaded. It holds the facts a visitor is most likely to need: identity and
public contact, summary, career goals, education, experience, project summaries,
skills, achievements, known gaps, privacy categories, and data freshness. The
seven substantial projects have individual entries; the four smallest share one
"Other projects" entry to keep the core compact, with their detail in
`sections/other_projects.md`. The core is currently about **2,781 estimated
tokens** (characters / 4). The target was 2,500 and the hard test limit is
3,000.

Fidelity rules used while writing it:

- Every fact comes from `knowledge/source/tisha_varma_knowledge_base.txt`.
- Numbers are copied exactly (`CPI 9.01`, `181 automated pytest tests`,
  `12 states`, `128,687 interactions`).
- Proficiency words appear only where the source states them (`working level`,
  `2022 internship level`, `Workchain`, `academic coursework`). Everything else
  is under `skills_listed_unrated` with the note "listed on her profile;
  proficiency level not specified".
- `known_gaps` quotes only limitations the source states.
- `private_do_not_share` holds category names, never values.
- `data_freshness` keeps the "as of" dates for PR statuses, Career Copilot
  usage, and the B.E. CPI.
- The Daily Planner teammates are called "two teammates"; their real names do
  not appear in the core or the sections.

### Tier 2: `knowledge/sections/`

Loaded on demand. Each project with substantial detail has a Markdown deep dive;
the smaller projects share `other_projects.md`. `sections/index.json` maps every
section id to a title, lowercase keyword list, and one-line description. The
`deep_dive_id` on each project must match an index key; a test enforces this.

### Router: `select_sections()`

`app/knowledge_router.py`:

- scores the latest user message by counting keyword hits (word-boundary
  matching, so `rag` does not match inside `storage` and `pr` does not match
  inside `profile`)
- if the latest user message has no hit, scores the previous user message
  instead, which keeps short follow-ups like "how did you test it?" on topic
  (for example "tell me about RazorFlow payments" -> `razorflow_ai`)
- ignores assistant messages entirely
- returns the top `max_sections` ids (default 2) with a score of at least 1
- returns `[]` for general questions, because the core already has project
  summaries

```python
from app.knowledge_router import select_sections

select_sections([{"role": "user", "content": "what is her CGPA"}])          # []
select_sections([{"role": "user", "content": "tell me about RazorFlow payments"}])
# ["razorflow_ai"]
```

### `TODO_FOR_TISHA.md`

Items that could not be grounded in the source live in
`backend/knowledge/TODO_FOR_TISHA.md`: skill proficiency levels, tools that are
never mentioned (Docker, Kubernetes, AWS/Azure, CI/CD, Redis, message queues),
the meaning of "production-oriented", the teammates' names, missing roles and
links, and the public email confirmation. **This file is documentation only and
is never loaded into the prompt.**

## How prompt building works

`build_system_prompt(section_ids=None)`:

1. reads `prompts/system_prompt.md`
2. reads and parses `knowledge/core.json` (invalid JSON raises
   `ProfileParseError` with the line and column)
3. checks the required sections, then validates the shape with pydantic
   (`ProfileValidationError`, e.g. "core.json is missing required
   section(s): known_gaps"). In `ENVIRONMENT=production`, empty `projects` or
   `skills` is rejected too
4. serialises the core as compact JSON (`separators=(",", ":")`, no indent) and
   injects it into `{{KNOWLEDGE_BASE}}`
5. reads `knowledge/sections/index.json`, rejects unknown section ids, and
   appends the selected Markdown under
   `DEEP DIVE DETAILS (also part of the knowledge base)`
6. fills `{{OWNER_NAME}}` and `{{CONTACT_EMAIL}}` from settings
7. raises `PromptBuildError` if the template uses an unknown placeholder.
   Substitution is single-pass, so braces inside project descriptions are safe
8. logs the approximate token count, at INFO, for the core alone and for the
   core plus the selected sections
9. logs a warning, via `logging` (not `print`), when the core still contains
   `PLACEHOLDER:` text; `ENVIRONMENT=production` raises instead

The finished prompt is cached in memory. The cache key includes the template,
core, index, and selected section modification times, the sorted section ids,
plus `OWNER_NAME`, `CONTACT_EMAIL`, and `ENVIRONMENT`, so edits are picked up on
the next build without a restart.

```python
from app.prompt_builder import build_system_prompt, reload_system_prompt

prompt = build_system_prompt()                     # core only, cached
prompt = build_system_prompt(["razorflow_ai"])     # core + one deep dive
prompt = reload_system_prompt(["workchain"])       # clear cache, re-read files
```

Tests and tooling can bypass the cache with `build_system_prompt(..., use_cache=False)`
and can pass explicit `prompt_path`, `core_path`, `sections_dir`, `index_path`,
and `settings`.

Current measured estimates with the real files:

| Prompt | Estimated tokens |
| --- | --- |
| Core alone (`len(compact_json) / 4`) | 2,781 |
| Full template + core | 4,771 |
| Full template + core + largest section (`razorflow_ai`) | 6,627 |

## Updating the knowledge base

Edit `knowledge/core.json` for facts that should always be available, and the
matching Markdown file in `knowledge/sections/` for detail that should only load
on demand. If you add a section, add it to `sections/index.json` and point a
project's `deep_dive_id` at it.

Keep the section names intact: the pydantic model and the prompt rely on them.
If you ever introduce fake data, prefix it with `PLACEHOLDER:`; development logs
a warning and production refuses to start. Record anything you cannot ground in
the source in `TODO_FOR_TISHA.md`, not in the knowledge base.

`backend/prompts/system_prompt.md` and `backend/tests/honesty_questions.md` are
working drafts. Replace them with the final reviewed text before launch; the
prompt must keep the three placeholders (`{{OWNER_NAME}}`, `{{CONTACT_EMAIL}}`,
`{{KNOWLEDGE_BASE}}`) or the build will fail loudly.

## Configuration reference

Every setting is validated at startup. Invalid values fail fast with a message
that names the variable but never includes secret values. Settings defined for
later stages are already validated here and will be consumed as those features
land.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GROQ_API_KEYS` | yes (or legacy `GROQ_API_KEY`) | - | Comma-separated Groq keys, stored as a `SecretStr` list |
| `GROQ_MODEL` | yes | - | Primary model id, never hardcoded |
| `GROQ_FALLBACK_MODELS` | no | empty | Comma-separated fallback model ids |
| `TEMPERATURE` | no | `0.2` | Sampling temperature, 0-2 |
| `MAX_OUTPUT_TOKENS_CHAT` | no | `600` | Chat reply token cap |
| `MAX_OUTPUT_TOKENS_FIT` | no | `1400` | JD fit token cap |
| `MAX_MESSAGE_CHARS` | no | `1500` | Single message length limit |
| `MAX_HISTORY_MESSAGES` | no | `8` | Recent messages kept in the prompt |
| `MAX_HISTORY_CHARS` | no | `6000` | History character budget |
| `MAX_JD_CHARS` | no | `8000` | Job description length limit |
| `MAX_UPLOAD_BYTES` | no | `2000000` | Uploaded file size limit |
| `RATE_LIMIT_PER_MINUTE` | no | `10` | Chat requests per IP per minute |
| `RATE_LIMIT_FIT_PER_MINUTE` | no | `3` | Fit requests per IP per minute |
| `RATE_LIMIT_PER_DAY_PER_IP` | no | `60` | Requests per IP per day |
| `GLOBAL_DAILY_REQUEST_CAP` | no | `400` | Requests across all visitors per day |
| `MAX_CONCURRENT_LLM_CALLS` | no | `4` | Concurrent model calls |
| `ENVIRONMENT` | no | `development` | `production` rejects placeholder knowledge and empty projects/skills |
| `TRUSTED_PROXY` | no | `false` | Trust `X-Forwarded-For` from the proxy |
| `MAINTENANCE_MODE` | no | `false` | Return 503 while maintenance is in progress |
| `KNOWLEDGE_DIR` | no | `backend/knowledge` | Knowledge base root |
| `SERVE_FRONTEND` | no | `true` | Serve the static frontend from FastAPI |
| `PUBLIC_URL` | no | empty | Public origin for social preview tags |
| `ALLOWED_ORIGINS` | no | localhost:5173, localhost:3000 | CORS origins, comma-separated |
| `CONTACT_EMAIL` | yes | - | Only contact detail the bot may share |
| `OWNER_NAME` | no | `Tisha` | Name injected into the prompt |

## Next phases (not implemented here)

1. Streaming responses and CORS configuration for the frontend origin.
2. Chat UI in `frontend/`.
3. JD matching endpoint, using `MAX_JD_CHARS` and the prompt's matching rules.

# Portfolio AI Chatbot

A public chatbot where recruiters can ask about Tisha's projects, skills, CGPA,
and fit for a job description. The guiding rule is **honesty over polish**: the
bot may only state facts that exist in `backend/knowledge/profile.json`.

This phase builds the foundation only:

- configuration loading with fail-fast validation
- a versioned system prompt template
- a knowledge base with clearly fake placeholder data
- a prompt builder that injects the knowledge base and guards placeholders
- tests and an honesty checklist

There is **no API, no Groq call, and no frontend** yet. FastAPI, Uvicorn, and
the Groq SDK are listed in `requirements.txt` for later phases but are unused.

## Folder layout

```text
portfolio-chatbot/
  backend/
    app/
      __init__.py          Package marker and version.
      config.py            Pydantic settings, cached get_settings().
      prompt_builder.py    Loads template + profile, renders the system prompt.
    prompts/
      system_prompt.md     Prompt template with {{OWNER_NAME}}, {{CONTACT_EMAIL}},
                           and {{KNOWLEDGE_BASE}}.
    knowledge/
      profile.json         Public facts about Tisha. Placeholder data for now.
    tests/
      test_prompt_builder.py   Pytest suite for config and prompt building.
      honesty_questions.md     Adversarial checklist with expected behavior.
    conftest.py            Puts `backend/` on sys.path for `pytest`.
    requirements.txt
  frontend/                Reserved for the chat UI. Empty for now.
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

- `GROQ_API_KEY` - real key from <https://console.groq.com/keys>
- `GROQ_MODEL` - the exact model id you want to use
- `CONTACT_EMAIL` - the public address visitors should use

The project `.env` is the only dotenv file loaded; its path is derived from
`config.py`, so it works from any working directory. Real environment
variables override it. The file is gitignored, and `groq_api_key` is stored as
a `SecretStr`, so it renders as `**********` in logs and tracebacks.

`get_settings()` raises a `RuntimeError` naming any missing variable, so a
misconfigured environment fails immediately instead of at request time.

## Run the tests

The tests do not call Groq and do not need a real API key.

```bash
cd backend
python -m pytest -q
```

Or from the project root:

```bash
python -m pytest backend -q
```

## How prompt building works

`build_system_prompt()`:

1. reads `prompts/system_prompt.md`
2. reads and parses `knowledge/profile.json` (invalid JSON raises
   `ProfileParseError` with the line and column)
3. pretty-prints the profile with `json.dumps(..., indent=2)` and injects it
   into `{{KNOWLEDGE_BASE}}`
4. fills `{{OWNER_NAME}}` and `{{CONTACT_EMAIL}}` from settings
5. raises `PromptBuildError` if any `{{PLACEHOLDER}}` is unknown or left over
6. logs a warning, via `logging` (not `print`), when the knowledge base still
   contains `PLACEHOLDER:` text

The finished prompt is cached in memory per template/knowledge pair. During
development, call `reload_system_prompt()` (or `clear_prompt_cache()`) to pick
up edits without restarting the process:

```python
from app.prompt_builder import build_system_prompt, reload_system_prompt

prompt = build_system_prompt()          # cached
prompt = reload_system_prompt()         # re-reads files, re-caches
```

Tests and tooling can bypass the cache with `build_system_prompt(use_cache=False)`
and can pass explicit `prompt_path`, `profile_path`, and `settings`.

## Replacing the placeholder data

Every fake value in `backend/knowledge/profile.json` starts with `PLACEHOLDER:`.
Search for that string and replace values section by section. Keep the section
names and the `private_do_not_share` list intact: the prompt relies on them.

`backend/prompts/system_prompt.md` and `backend/tests/honesty_questions.md` are
working drafts. Replace them with the final reviewed text before launch; the
prompt must keep the three placeholders (`{{OWNER_NAME}}`, `{{CONTACT_EMAIL}}`,
`{{KNOWLEDGE_BASE}}`) or the build will fail loudly.

## Configuration reference

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GROQ_API_KEY` | yes | - | Groq authentication, stored as `SecretStr` |
| `GROQ_MODEL` | yes | - | Model id, never hardcoded |
| `ALLOWED_ORIGINS` | no | localhost:5173, localhost:3000 | CORS origins, comma-separated |
| `MAX_JD_CHARS` | no | 8000 | Job description length limit |
| `MAX_MESSAGE_CHARS` | no | 1500 | Single message length limit |
| `CONTACT_EMAIL` | yes | - | Only contact detail the bot may share |
| `OWNER_NAME` | no | `Tisha` | Name injected into the prompt |

## Next phases (not implemented here)

1. FastAPI backend with streaming responses and CORS.
2. Groq client using `settings.groq_model`.
3. Chat UI in `frontend/`.
4. JD matching endpoint, using `MAX_JD_CHARS` and the prompt's matching rules.

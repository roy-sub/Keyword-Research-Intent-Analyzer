# Keyword Suggest & Intent Analyzer — Backend

Internal tool. Given a seed topic it collects Google autocomplete
suggestions, merges them into a deduplicated keyword dataset, sends that
dataset to Google's Gemini API for a search-intent analysis, and returns both
the raw data and the AI report as JSON. It also serves the static frontend at
`/`, so the UI and the API share one origin and one Render web service.

---

## Quick start

Two commands, from the repo root, after creating your `.env`:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open **http://localhost:8000** and sign in with the `ADMIN_USERNAME` /
`ADMIN_PASSWORD` you put in `.env`.

Create the `.env` first:

```bash
cp .env.example .env
# then edit .env and set ADMIN_PASSWORD and GEMINI_API_KEY
```

`.env.example` documents every variable with a comment and a safe default —
only `ADMIN_PASSWORD` and `GEMINI_API_KEY` have no default and must be filled
in. Startup fails with a clear message naming any required variable that is
missing.

### UI without a backend

`frontend/index.html?mock=1` bypasses login and renders
`frontend/mock-response.json` — useful for frontend work with no API key.

### Tests

```bash
./.venv/bin/python -m pytest
```

All HTTP is mocked; no test touches Google or Gemini, and none needs real
credentials.

---

## Layout

```
app/
  main.py           FastAPI app, routes, static mount, lifespan
  config.py         settings, env vars, MODE handling, fail-fast validation
  auth.py           login, session tokens, API-key enforcement
  rate_limit.py     global sliding-window search quota
  cache.py          TTL + LRU cache
  models.py         request/response models (the API contract)
  analysis.py       Gemini client and prompt
  suggest/
    base.py         SuggestProvider protocol, query set, dedupe, merge
    google.py       GoogleSuggestProvider
frontend/           static UI, served at /
tests/
render.yaml
.env.example
```

---

## Environment variables

Every variable, its default, and what it does. All are read through
`pydantic-settings` from the environment, falling back to `.env`.

### Mode

| Variable | Default | Purpose |
|---|---|---|
| `MODE` | `dev` | `dev` or `prod`. See the table below. |

|  | `dev` | `prod` |
|---|---|---|
| Frontend at `/` | yes | yes |
| CORS | any `http://localhost:*` / `http://127.0.0.1:*` | exactly `ALLOWED_ORIGINS` |
| `X-API-Key` | not enforced | required for callers outside `ALLOWED_ORIGINS` |
| `/docs`, `/redoc`, `/openapi.json` | enabled | disabled (404) |
| Logs | verbose, with module and line | concise |
| Startup validation | `ADMIN_PASSWORD`, `GEMINI_API_KEY` | those plus `API_KEY`, `ALLOWED_ORIGINS` |

Dev works locally with zero extra setup beyond the two required secrets.

### Access

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | The single admin login. There are no other accounts. |
| `ADMIN_PASSWORD` | **required** | No default. Startup fails without it. |
| `SESSION_TTL_HOURS` | `12` | How long an issued session token stays valid. |
| `API_KEY` | **required in prod** | Guards the API against callers outside the app. |
| `ALLOWED_ORIGINS` | empty | Comma-separated origins allowed in prod, e.g. `https://keyword-analyzer.onrender.com`. |

### AI

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | **required** | No default. From https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-3.5-flash` | Model used for the report. |
| `GEMINI_TIMEOUT_SECONDS` | `120` | Per-call timeout. |
| `MAX_KEYWORDS_IN_PROMPT` | `600` | Prompt cap; truncation is noted in the prompt itself. |

### Collection

| Variable | Default | Purpose |
|---|---|---|
| `SUGGEST_LANG` | `en` | Autocomplete `hl`. |
| `SUGGEST_COUNTRY` | `us` | Autocomplete `gl`. |
| `SUGGEST_DELAY_SECONDS` | `1.0` | Delay between consecutive requests, plus random jitter. |
| `SUGGEST_TIMEOUT_SECONDS` | `5.0` | Per-request timeout. |
| `SUGGEST_MAX_RETRIES` | `2` | Retries per query on timeout / 429 / 5xx. |
| `SUGGEST_QUESTION_MODIFIERS` | `who,what,when,where,why,how` | Prefixed to the seed. |
| `SUGGEST_COMMERCIAL_MODIFIERS` | `best,buy,cheap,near me` | `near me` is appended; the rest prefixed. |

### Limits and logging

| Variable | Default | Purpose |
|---|---|---|
| `RATE_LIMIT_MAX_SEARCHES` | `10` | Fresh runs allowed per window, globally. |
| `RATE_LIMIT_WINDOW_MINUTES` | `60` | Window length. |
| `CACHE_TTL_MINUTES` | `1440` | How long a completed run is reused (24h). |
| `CACHE_MAX_ENTRIES` | `50` | Cached runs before LRU eviction. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

---

## Authentication

There is one user: the admin.

1. `POST /api/login` with `{"username", "password"}`. Both are compared with
   `secrets.compare_digest`. On success the server mints a random opaque
   token (`secrets.token_urlsafe(32)`), holds it in memory for
   `SESSION_TTL_HOURS`, and returns it as `access_key`.
2. The frontend stores **only that token** in `sessionStorage` — never the
   password — and sends it as `X-Access-Key` on every other `/api/*` call.
3. `POST /api/logout` invalidates the token. A 401 anywhere clears the stored
   token and returns the user to the sign-in gate.

A failed login costs a constant ~0.75s delay, to blunt brute forcing.

### About `API_KEY`

When `MODE=prod`, a request whose `Origin` is not in `ALLOWED_ORIGINS` must
also carry `X-API-Key`. Same-origin requests from the served frontend pass
without it: a browser sends `Origin` on cross-origin requests and same-origin
POSTs, and `Sec-Fetch-Site: same-origin` on same-origin GETs.

**A key that the browser can see is never truly secret.** Anyone who can open
the site can read the network tab. `API_KEY` is a nuisance gate that keeps
casual external callers and scrapers off the endpoint — nothing more. **The
login is the real access control.** Treat `ADMIN_PASSWORD` as the secret that
matters, and rotate it rather than the API key if you suspect a leak.

---

## API contract

All bodies are JSON. Auth is `X-Access-Key: <token>` unless noted.

### `GET /api/health` — no auth

```json
{"status": "ok"}
```

Touches nothing upstream; this is Render's health check path. It stays
responsive during a 40-second run — the collection pipeline never blocks the
event loop (verified: health answered in ~1ms throughout a full 37-query run).

### `GET /api/status`

```json
{
  "searches_remaining": 7,
  "window_minutes": 60,
  "retry_after_seconds": 0,
  "request_delay_seconds": 1.0,
  "expected_queries": 37
}
```

### `POST /api/analyze`

Request: `{"topic": "luxury villa rentals"}`

```json
{
  "topic": "luxury villa rentals",
  "generated_at": "2026-09-18T09:14:03Z",
  "cached": false,
  "duration_seconds": 42.7,
  "total_keywords": 284,
  "queries_attempted": 37,
  "queries_succeeded": 37,
  "failed_queries": [],
  "searches_remaining": 6,
  "sources": [
    {"query": "luxury villa rentals", "type": "seed", "keywords": ["luxury villa rentals italy"]},
    {"query": "luxury villa rentals a", "type": "alphabet", "keywords": ["luxury villa rentals amalfi coast"]}
  ],
  "keywords": [
    {"keyword": "luxury villa rentals italy", "sources": ["luxury villa rentals"], "types": ["seed"]}
  ],
  "analysis_markdown": "## 1. Search Intent Classification\n..."
}
```

- `type` is one of `seed`, `alphabet`, `question`, `commercial`.
- `sources` preserves the exact query that produced each suggestion. It is
  the client's verification data and is never flattened away.
- `keywords` is the deduplicated union, sorted alphabetically
  (case-insensitive), each entry carrying every source query and type that
  produced it. Dedupe is case-insensitive and keeps the first occurrence's
  casing.

Errors:

| Status | Body |
|---|---|
| 400 | `{"detail": "Topic must not be empty."}` / `{"detail": "Topic must be 100 characters or fewer."}` |
| 401 | `{"detail": "Invalid or missing access key."}` / `{"detail": "Invalid or missing API key."}` |
| 429 | `{"detail": "Search limit reached.", "retry_after_seconds": 1840}` plus a `Retry-After` header |
| 502 | `{"detail": "Google returned no suggestions."}` / `{"detail": "AI analysis failed: <reason>"}` |
| 504 | `{"detail": "The run timed out."}` |

No key, traceback, or upstream response body ever appears in `detail`.

### `POST /api/login`, `POST /api/logout`

See **Authentication** above.

### `GET /` and static assets

`frontend/` is served via `StaticFiles` with `index.html` at the root. API
routes are registered before the static mount, so `/api/*` always wins.

---

## How collection works

- `https://suggestqueries.google.com/complete/search` over HTTPS, with
  `client=firefox`, `q`, `hl`, `gl`. Suggestions are at index 1 of the array.
- **Query set (37 by default):** the seed alone, the seed plus each letter
  a–z, then the question and commercial modifiers. Modifier lists come from
  config, not from the code.
- **Pacing:** requests run strictly sequentially with `SUGGEST_DELAY_SECONDS`
  plus up to 30% random jitter between each pair. Never concurrent. This is a
  deliberate requirement, not an accident of implementation.
- **Retries:** per-request timeout, then exponential backoff on timeouts,
  429s and 5xx, honouring `Retry-After` when present.
- **Decoding:** the endpoint does not always return UTF-8. The declared
  charset is tried first, then UTF-8, then latin-1, then a replacement
  decode. It never raises on a mis-labelled body.
- **Failures are never swallowed.** Every failed query is logged with its
  status and lands in `failed_queries`. Some failing → partial results with
  accurate counts. All failing → 502.

Swapping providers: routes depend on the `SuggestProvider` protocol in
`app/suggest/base.py`, not on Google. A paid provider (SerpApi, DataForSEO)
only needs `async def fetch(topic, lang, country) -> list[SourceResult]` and
a one-line swap in `build_state()` in `app/main.py`.

---

## Changing things without touching code

**The AI model** — edit `GEMINI_MODEL` in `.env` (or in Render's environment)
and restart. No model name appears anywhere in the code.

> `gemini-2.5-flash` is being retired — do not use it. The default is
> `gemini-3.5-flash`. Check Google's current model list before changing it.

**The rate limit** — edit `RATE_LIMIT_MAX_SEARCHES` and
`RATE_LIMIT_WINDOW_MINUTES` and restart.

**The pacing, timeouts, market, or modifier words** — the `SUGGEST_*`
variables. Changing the delay or timeout automatically widens the per-run
timeout budget, so a slower run cannot start timing out by surprise.

---

## Deploying to Render

1. Push this repo to GitHub.
2. In Render, **New → Blueprint**, point it at this repo. `render.yaml`
   defines one web service.
3. Render prompts for the values marked `sync: false`: `ADMIN_PASSWORD`,
   `API_KEY`, `GEMINI_API_KEY`, `ALLOWED_ORIGINS`. Set `ALLOWED_ORIGINS` to
   the service's own public URL, e.g.
   `https://keyword-analyzer.onrender.com`.
4. Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   Health check path: `/api/health`

`MODE` is set to `prod` in the blueprint, so docs are disabled, CORS is
locked to `ALLOWED_ORIGINS`, and the API key is enforced for outside callers.

---

## Re-syncing the frontend

**The frontend repo is the source of truth:**
https://github.com/roy-sub/Keyword-Research-Intent-Analyzer-Frontend

`frontend/` here is a copy, so that this repo alone is deployable. After a
change lands there:

```bash
git clone https://github.com/roy-sub/Keyword-Research-Intent-Analyzer-Frontend.git /tmp/kria-fe
cp /tmp/kria-fe/index.html /tmp/kria-fe/styles.css /tmp/kria-fe/app.js /tmp/kria-fe/mock-response.json frontend/
git add frontend && git commit -m "Sync frontend from source repo" && git push
```

Never edit `frontend/` here directly — the next sync would overwrite it.

---

## Known limitations

- **Quota and cache are in-process and reset on restart.** A Render redeploy
  or a cold start clears both the search quota and every cached run, and
  signs the admin out. This is accepted for V1: it assumes a single
  instance. Running more than one instance means moving the rate limiter,
  the cache, and the session store to Redis — they are already isolated
  behind small classes, so that is a contained change.
- **The Google suggest endpoint is unofficial.** It is undocumented,
  unsupported, and may change shape, rate-limit, or block outright at any
  time — and it throttles cloud IP ranges considerably harder than
  residential ones. A run from Render may see more `failed_queries` than the
  same run from a laptop, or may fail entirely with a 502 where local runs
  succeed. That is the endpoint's behaviour, not a bug. If it becomes
  unusable, swap in a paid provider behind `SuggestProvider`.
- **The rate limit is global, not per user.** There is one shared login, so
  the limit protects the upstream endpoints rather than individual callers.
- **The browser-delivered `API_KEY` is not a secret.** See
  **Authentication** above.

---

## Logging

One structured line per run:

```
run topic='villa rentals' duration=42.7s queries=37/37 keywords=284 cached=False quota_remaining=6
```

Credentials are never logged, never returned in an error body, and never put
into a prompt.

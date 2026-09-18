# Backend — Refract

FastAPI service. Given a seed topic it collects Google autocomplete
suggestions, merges them into a deduplicated keyword dataset, sends that
dataset to Google's Gemini API for a search-intent analysis, and returns both
the raw data and the AI report as JSON.

**This service is API-only.** The UI is a separate Render service in
[`../frontend`](../frontend), on its own origin, so every browser call is
cross-origin and `ALLOWED_ORIGINS` is load-bearing. See the
[root README](../README.md) for the overall architecture.

---

## Quick start

From this `backend/` folder:

```bash
cp .env.example .env     # then set ADMIN_PASSWORD and GEMINI_API_KEY
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is then at **http://localhost:8000**, with interactive docs at
**http://localhost:8000/docs** (dev only).

`.env.example` documents every variable with a comment and a safe default.
Only `ADMIN_PASSWORD` and `GEMINI_API_KEY` have no default and must be filled
in — startup fails with a clear message naming any required variable that is
missing.

To use the UI as well, start the frontend in a second terminal — see
[frontend/README.md](../frontend/README.md).

### Testing it

```bash
./.venv/bin/python -m pytest
```

66 tests. All HTTP is mocked; nothing touches Google or Gemini, and no test
needs real credentials.

Smoke-testing a running server by hand:

```bash
# health — no auth, no upstream calls
curl localhost:8000/api/health
# -> {"status":"ok"}

# log in (use your own ADMIN_PASSWORD)
TOKEN=$(curl -s -X POST localhost:8000/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<your ADMIN_PASSWORD>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_key'])")

# quota and pacing
curl localhost:8000/api/status -H "X-Access-Key: $TOKEN"

# a real run — takes ~40s and consumes one unit of quota
curl -X POST localhost:8000/api/analyze \
  -H 'Content-Type: application/json' -H "X-Access-Key: $TOKEN" \
  -d '{"topic":"luxury villa rentals"}'
```

---

## Layout

```
backend/
  app/
    main.py           FastAPI app, routes, lifespan
    config.py         settings, env vars, MODE handling, fail-fast validation
    auth.py           login, session tokens, API-key enforcement
    rate_limit.py     global sliding-window search quota
    cache.py          TTL + LRU cache
    models.py         request/response models (the API contract)
    analysis.py       Gemini client and prompt
    suggest/
      base.py         SuggestProvider protocol, query set, dedupe, merge
      google.py       GoogleSuggestProvider
  tests/
  .env.example
  requirements.txt
  pytest.ini
```

---

## Environment variables

All read through `pydantic-settings` from the environment, falling back to
`.env` in this folder.

### Mode

| Variable | Default | Purpose |
|---|---|---|
| `MODE` | `dev` | `dev` or `prod`. See below. |

|  | `dev` | `prod` |
|---|---|---|
| CORS | any origin | exactly `ALLOWED_ORIGINS` |
| `X-API-Key` | not enforced | required for callers outside `ALLOWED_ORIGINS` |
| `/docs`, `/redoc`, `/openapi.json` | enabled | disabled (404) |
| Logs | verbose, with module and line | concise |
| Startup validation | `ADMIN_PASSWORD`, `GEMINI_API_KEY` | those plus `API_KEY`, `ALLOWED_ORIGINS` |

Dev needs no CORS configuration: **any** origin is accepted, so it does
not matter whether you open the UI at `localhost`, `127.0.0.1`, `0.0.0.0`,
or a LAN IP from your phone. Dev does not enforce the API key either, so an
allowlist would buy nothing there. `MODE=prod` is strict.

### Access

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | The single admin login. There are no other accounts. |
| `ADMIN_PASSWORD` | **required** | No default. Startup fails without it. |
| `SESSION_TTL_HOURS` | `12` | How long an issued session token stays valid. |
| `API_KEY` | **required in prod** | Guards the API against callers outside the app. |
| `ALLOWED_ORIGINS` | empty | **Required in prod.** Comma-separated origins allowed to call this API — in practice, the frontend service's URL. |

`ALLOWED_ORIGINS` accepts a bare hostname and promotes it to `https://<host>`,
so it can be wired straight from Render's `fromService: property: host`.
`https://a.example, b.example` parses as
`["https://a.example", "https://b.example"]`.

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
also carry `X-API-Key`. Because the frontend is its own service on its own
origin, every legitimate browser call carries an `Origin` header, so the rule
is simply: a listed origin is the app and passes; everything else — curl, a
script, another site — needs the key.

**A key that the browser can see is never truly secret.** Anyone who can open
the site can read the network tab. `API_KEY` is a nuisance gate that keeps
casual external callers and scrapers off the endpoint — nothing more. **The
login is the real access control.** Treat `ADMIN_PASSWORD` as the secret that
matters, and rotate it rather than the API key if you suspect a leak.

---

## API contract

All bodies are JSON. Auth is `X-Access-Key: <token>` unless noted.

### `GET /` — no auth

A small descriptor, so hitting the API host in a browser explains itself
instead of returning a bare 404.

```json
{"service": "refract-api", "status": "ok", "docs": "/docs"}
```

`docs` is `null` in prod.

### `GET /api/health` — no auth

```json
{"status": "ok"}
```

Touches nothing upstream; this is Render's health check path. It stays
responsive during a 40-second run — the collection pipeline never blocks the
event loop (verified: health answered in ~1 ms throughout a full 37-query
run).

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

Deployed by the root `render.yaml` as `keyword-analyzer-api`, or manually:

| Setting | Value |
|---|---|
| Type | Web Service |
| Root directory | `backend` |
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/api/health` |

Set `MODE=prod`, the three secrets (`ADMIN_PASSWORD`, `API_KEY`,
`GEMINI_API_KEY`), and `ALLOWED_ORIGINS` to the frontend service's URL. The
blueprint fills `ALLOWED_ORIGINS` in automatically from the frontend
service's host.

> If the UI loads but every call fails with a CORS error, `ALLOWED_ORIGINS`
> does not match the frontend's origin. The browser console names the origin
> that was rejected — paste exactly that, scheme included. A rejected CORS
> preflight appears in the backend log as `OPTIONS /api/... 400`, and the UI
> reports it as "Could not reach the server", which looks misleadingly like
> a credentials problem. Check the log before suspecting the password.

---

## Known limitations

- **Quota, cache and sessions are in-process and reset on restart.** A Render
  redeploy or a cold start clears both the search quota and every cached run,
  and signs the admin out. This is accepted for V1: it assumes a single
  instance. Running more than one instance means moving the rate limiter, the
  cache, and the session store to Redis — they are already isolated behind
  small classes, so that is a contained change.
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

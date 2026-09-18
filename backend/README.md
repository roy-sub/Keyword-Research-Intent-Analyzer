# Backend — Keyword Analyzer

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
cp .env.example .env     # then set ADMIN_PASSWORD + at least one AI key
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API is then at **http://localhost:8000**, with interactive docs at
**http://localhost:8000/docs** (dev only).

`.env.example` documents every variable with a comment and a safe default.
Only `ADMIN_PASSWORD` and at least one AI provider key have no default and
must be filled in — startup fails with a clear message naming any required
variable that is missing.

To use the UI as well, start the frontend in a second terminal — see
[frontend/README.md](../frontend/README.md).

### Testing it

```bash
./.venv/bin/python -m pytest
```

92 tests. All HTTP is mocked; nothing touches Google or Gemini, and no test
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
    analysis.py       prompt, the three providers, and the fallback chain
    markets.py        locale registry: language, country and modifier words
    pdf.py            Markdown → typeset PDF (ReportLab Platypus)
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
| Startup validation | `ADMIN_PASSWORD`, one AI key | those plus `API_KEY`, `ALLOWED_ORIGINS` |

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

Three providers, tried in order. The first that returns a report wins; any
failure falls through to the next. **At least one key is required** — startup
fails if all three are empty. An unset provider is skipped, not an error.

| Variable | Default | Purpose |
|---|---|---|
| `AI_PROVIDER_ORDER` | `anthropic,openai,gemini` | Order to try. Unknown names are ignored. |
| `AI_TIMEOUT_SECONDS` | `120` | Per-attempt timeout, applied to each provider separately. |
| `MAX_KEYWORDS_IN_PROMPT` | `600` | Prompt cap; truncation is noted in the prompt itself. |
| `ANTHROPIC_API_KEY` | — | Claude. https://console.anthropic.com/settings/keys |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Balanced tier. Stronger: `claude-opus-5`. Cheaper: `claude-haiku-4-5`. |
| `OPENAI_API_KEY` | — | OpenAI. https://platform.openai.com/api-keys |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Compact tier, 400K context. Stronger: `gpt-5.4`, `gpt-6-astra`. |
| `GEMINI_API_KEY` | — | Gemini. https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Most capable Flash model. `gemini-2.5-flash` is retiring — do not use it. |

> **Cost.** All three defaults are mid-tier models chosen to balance quality
> against price for a once-per-topic analysis. Move up or down a tier with the
> alternatives above — a config change, no code change. A cached run costs
> nothing at all, and the chain only reaches the second provider when the
> first fails.

**How the fallback behaves.** Each provider's SDK already retries connection
errors, 429s and 5xx once; beyond that there is no extra retry loop, because
with three providers the cascade *is* the retry. Failure reasons are curated
in `app/analysis.py` — the caller gets a short cause per provider and never an
upstream body, a traceback, or anything that could carry a credential.

### Collection

| Variable | Default | Purpose |
|---|---|---|
| `ENABLED_MARKETS` | `en-US,de-CH` | Which locales the picker offers. Unknown codes are dropped rather than raising. |
| `DEFAULT_MARKET` | `en-US` | Which one is preselected; switchable per run in the UI. |
| `SUGGEST_DELAY_SECONDS` | `1.0` | Delay between consecutive requests, plus random jitter. |
| `SUGGEST_TIMEOUT_SECONDS` | `5.0` | Per-request timeout. |
| `SUGGEST_MAX_RETRIES` | `2` | Retries per query on timeout / 429 / 5xx. |

Modifier words are **not** environment variables: they belong to a market, not
to a deployment. See **Markets** below.

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
{"service": "keyword-analyzer-api", "status": "ok", "docs": "/docs"}
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
  "analysis_markdown": "## 1. Search Intent Classification\n...",
  "analysis_provider": "anthropic",
  "analysis_model": "claude-sonnet-5"
}
```

- `type` is one of `seed`, `alphabet`, `question`, `commercial`.
- `sources` preserves the exact query that produced each suggestion. It is
  the client's verification data and is never flattened away.
- `analysis_provider` is `anthropic`, `openai` or `gemini` — **which provider
  actually produced the report**, not necessarily the first choice.
  `analysis_model` is the exact model id it used. Both are stored with a
  cached run, so a cached result reports the provider that originally ran it.
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
| 502 | `{"detail": "Google returned no suggestions."}` |
| 502 | `{"detail": "The AI analysis could not be completed.", "provider_failures": [{"provider","label","model","reason"}, ...]}` — every provider failed, one reason each |
| 504 | `{"detail": "The run timed out."}` |

No key, traceback, or upstream response body ever appears in `detail`.

### `POST /api/export/report.pdf`

Typesets a report the client already holds into a PDF. Returns
`application/pdf` with a `Content-Disposition` attachment filename.

Request:

```json
{
  "topic": "luxury villa rentals",
  "analysis_markdown": "## 1. Search Intent Classification\n...",
  "generated_at": "2026-09-18T09:14:03Z",
  "provider_label": "Claude",
  "model": "claude-sonnet-5",
  "total_keywords": 150,
  "queries_succeeded": 35,
  "queries_attempted": 37
}
```

Only `topic` and `analysis_markdown` are required; the rest fill the facts
strip under the title and are omitted from the document if absent.

**Why the client posts the report back instead of naming a run.** Re-running
the analysis to export it would cost a quota slot and could return different
prose than the one on screen, and caching rendered PDFs server-side would
mean holding user content for no reason. So this route **costs no quota,
makes no upstream call, and stores nothing** — it is a pure function from the
posted body to bytes.

**The Markdown is untrusted.** It arrives over the wire, so every string is
escaped with `xml.sax.saxutils.escape` before any inline formatting is
applied, and the renderer is tested against malformed tables, unclosed
emphasis, deep headings, unbreakable tokens and embedded ReportLab markup.
None of it may raise. The filename is slugified to quote-free ASCII because
it is interpolated into a response header.

Rendering is ReportLab Platypus on a worker thread (`asyncio.to_thread`), so
a long report cannot block the event loop while a run is in flight.

Errors:

| Status | Body |
|---|---|
| 401 | `{"detail": "Invalid or missing access key."}` |
| 422 | Pydantic validation — empty topic or empty report |
| 500 | `{"detail": "The PDF could not be built."}` |

### `POST /api/login`, `POST /api/logout`

See **Authentication** above.

---

## Markets

A market is a **language, a country, and the modifier words that suit them**,
bundled together and chosen per run.

They are bundled because separating them is the trap. Setting `hl=de` while
still firing `how`, `what` and `near me` at a German topic returns almost
nothing for ten of the 37 queries — the run looks like it worked, and quietly
came back a third thinner. The words have to move with the language, so they
live in the same object.

| Code | Label | `hl` / `gl` | Question words | Buying words |
|---|---|---|---|---|
| `en-US` | English · United States | `en` / `us` | who, what, when, where, why, how | best, buy, cheap, near me |
| `de-CH` | Deutsch · Schweiz | `de` / `ch` | wer, wie, was, wo, warum, welche | beste, mieten, buy, best |
| `de-DE` | Deutsch · Deutschland | `de` / `de` | wer, wie, was, wo, warum, welche | beste, kaufen, günstig, mieten |
| `en-GB` | English · United Kingdom | `en` / `gb` | who, what, when, where, why, how | best, buy, cheap, near me |

`de-CH`'s word lists are taken verbatim from the client briefing.

**Rules the registry holds to**, each covered by a test:

- **Every market is 37 queries** — seed, 26 letters, 6 question words, 4
  buying words. That keeps two markets directly comparable, and means the
  progress estimate, the run timeout and the expected counts need no
  per-market special case.
- **No market mixes languages** in its question words.
- **Position is declared, not guessed.** Modifiers are prefixes, as in the
  client's reference code. The handful that read as suffixes (`near me`) are
  listed explicitly, so a German modifier is never mis-positioned by an
  English heuristic like `startswith("near")`.
- **`Accept-Language` is sent per request**, not set on the shared client — a
  client-wide header would pin every run to whichever market happened to be
  the default at startup.
- **The cache is keyed on the market**, not on `hl`/`gl`. Two markets can
  share a language and still produce different keywords, so `de-CH` must
  never be served a `de-DE` cache entry.
- **Unknown codes fall back, they do not fail.** A stale client or an old
  bookmark resolves to the default rather than 400-ing. A market that is not
  in `ENABLED_MARKETS` is refused even if the code is valid.
- **The prompt names the market**, so the model is told it is reading German
  and does not mistake compounds for noise or try to translate them. Keywords
  are quoted in their original language; the report itself stays in English
  so the four headings remain exactly as the briefing specifies.

Adding a market is one entry in `app/markets.py` and one code in
`ENABLED_MARKETS`. Nothing else changes.

---

## Proving the data is really Google's

`scripts/verify_live.py` exists so the keyword list never has to be taken on
trust. It asks Google directly using **only the standard library — no code
from this project** — then asks the same question through the app's own
collection path, and compares.

```bash
python scripts/verify_live.py "ski chalet zermatt"
python scripts/verify_live.py "chalet zermatt mieten" --market de-CH
python scripts/verify_live.py "bundesliga top scorers" --full   # all 37, ~40s
```

It prints the raw bytes Google returned, so the comparison is auditable rather
than asserted. If the app ever reported a keyword Google did not return, the
script names it and exits non-zero. It calls no AI provider, writes nothing,
and costs no quota — it does not go through the API, so the rate limiter is
not involved.

The comparison is asymmetric on purpose: **extra** keywords would prove
fabrication, but **missing** ones prove nothing, because autocomplete is
genuinely not deterministic and two calls a second apart can differ. Only the
extras fail the check.

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

**The AI model, or which provider leads** — edit `ANTHROPIC_MODEL`,
`OPENAI_MODEL`, `GEMINI_MODEL` or `AI_PROVIDER_ORDER` and restart. No model
name and no provider order appears anywhere in the code.

> `gemini-2.5-flash` is being retired — do not use it. Check each vendor's
> current model list before changing a default.

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

Set `MODE=prod`, the secrets (`ADMIN_PASSWORD`, `API_KEY`, and at least one
of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`), and
`ALLOWED_ORIGINS` to the frontend service's URL. The
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

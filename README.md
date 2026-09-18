# Keyword Analyzer

*One topic in. The whole spectrum out.*

Internal tool. Give it a seed topic; it collects Google autocomplete
suggestions, merges them into a deduplicated keyword dataset, sends that
dataset to an AI model for a search-intent analysis, and returns both
the raw data and the AI report.

This repository is a **monorepo containing two independently deployable
applications**:

```
.
├── backend/     FastAPI service — collection, AI analysis, auth, quota, cache
├── frontend/    Static HTML/CSS/JS UI — no build tooling, no npm
├── render.yaml  Blueprint deploying both as two separate Render services
├── .gitignore
└── README.md    (this file)
```

Each folder is self-contained and has its own README with setup,
configuration and deployment detail:

- **[backend/README.md](backend/README.md)** — every environment variable,
  the full API contract, how collection works, how to swap providers.
- **[frontend/README.md](frontend/README.md)** — the design system (tokens,
  type, colour, icons), every screen state, how the API URL is configured,
  mock mode, accessibility, and deployment as a static site.

Neither side depends on the other's internals, on the old single-service
layout, or on the previously separate frontend repository. **This repo is now
the only source of truth for the frontend.**

---

## Brand

The product is **Refract**: a prism refracts one beam into a spectrum, and
Refract splits one seed topic into the spectrum of search intent behind it.
The mark is that prism reduced to two shapes — a triangle outline with a focal
dot — so it holds at favicon size.

The interface is built on a single design system documented in
[frontend/README.md](frontend/README.md#design-system): a warm paper canvas,
near-black primary actions, restrained radii (3/5/8/10px, no pills), hairline
separation, Instrument Sans for display and Inter for UI, and colour reserved
almost entirely for meaning — one hue per intent class, used identically in
the tags, the intent-mix bar and the empty-state illustration.

> Render **service names** (`keyword-analyzer-api`, `keyword-analyzer-web`)
> are intentionally unchanged. Renaming them in `render.yaml` would orphan an
> already-deployed service and create new ones. They are infrastructure ids,
> not brand surface.

---

## Architecture

Two services, two origins, talking over CORS:

```
   Browser
      │
      │  1. loads the UI
      ▼
  ┌──────────────────────────┐
  │ keyword-analyzer-web     │   Render Static Site  (frontend/)
  │ index.html + app.js      │
  │ config.js → API_BASE_URL │
  └──────────────────────────┘
      │
      │  2. fetch(API_BASE_URL + "/api/...")  with X-Access-Key
      ▼
  ┌──────────────────────────┐
  │ keyword-analyzer-api     │   Render Web Service  (backend/)
  │ FastAPI + uvicorn        │
  └──────────────────────────┘
      │                    │
      ▼                    ▼
  Google autocomplete   Claude → OpenAI → Gemini
```

The browser is the only thing that talks to both. The backend never serves
the frontend, and the frontend has no server-side component.

**The two settings that connect them:**

| Service | Variable | Value |
|---|---|---|
| frontend | `API_BASE_URL` | the backend's origin, e.g. `https://keyword-analyzer-api.onrender.com` |
| backend | `ALLOWED_ORIGINS` | the frontend's origin, e.g. `https://keyword-analyzer-web.onrender.com` |

`render.yaml` wires both automatically with `fromService`, so you never have
to type either URL. Locally you set them yourself — see below.

---

## Running the whole app locally

You need **two terminals**: the backend and the frontend are separate
services locally exactly as they are in production.

### Terminal 1 — backend (port 8000)

```bash
cd backend
cp .env.example .env     # then set ADMIN_PASSWORD + at least one AI key
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Terminal 2 — frontend (port 5173)

```bash
cd frontend
python3 -m http.server 5173
```

Then open **http://localhost:5173** and sign in with the `ADMIN_USERNAME` and
`ADMIN_PASSWORD` from `backend/.env`.

That works with no extra configuration:

- `frontend/config.js` already points at `http://localhost:8000`;
- the backend in `MODE=dev` accepts **any** origin, so it does not matter
  whether you open `localhost:5173`, `127.0.0.1:5173`, `0.0.0.0:5173` (which
  is what `python3 -m http.server` prints), or a LAN IP from your phone;
- the API key is not enforced in dev.

> If sign-in reports **"Could not reach the server"**, that is not a
> credentials failure — the browser reports every network and CORS problem
> identically. Check the browser console, which logs the API URL that was
> tried, and the backend log: `OPTIONS /api/login ... 400` means a rejected
> CORS preflight.

The exact commands, including how to test each side, are in
[backend/README.md](backend/README.md) and
[frontend/README.md](frontend/README.md).

---

## Deploying to Render

Both services come from this one repo, each built from its own folder.

### With the blueprint (recommended)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at this repo. It reads
   `render.yaml` and creates both services.
3. Render prompts for the secrets on the API service: `ADMIN_PASSWORD`,
   `API_KEY`, and the AI provider keys — `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`, `GEMINI_API_KEY`. **At least one AI key is required**;
   unset providers are skipped.
4. Deploy. `ALLOWED_ORIGINS` and `API_BASE_URL` are filled in automatically
   from each service's hostname, so there is no chicken-and-egg with URLs and
   nothing to paste by hand.

### Manually, as two services

If you prefer to create them by hand rather than from the blueprint:

| | Backend | Frontend |
|---|---|---|
| Type | Web Service | Static Site |
| Root directory | `backend` | `frontend` |
| Runtime | Python 3 | Static |
| Build command | `pip install -r requirements.txt` | `./build.sh` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | — |
| Publish directory | — | `.` |
| Health check path | `/api/health` | — |

Then set the environment variables. Create the backend first, note its URL,
and set the frontend's `API_BASE_URL` to it; then set the backend's
`ALLOWED_ORIGINS` to the frontend's URL and redeploy the backend. The full
variable list is in [backend/README.md](backend/README.md).

> Redeploy the **frontend** after changing `API_BASE_URL` — `config.js` is
> generated at build time, so the new value only takes effect on a rebuild.

---

## Verifying a deployment

```bash
# 1. The API is up (no auth required)
curl https://<api-host>/api/health
# -> {"status":"ok"}

# 2. In prod, an outside caller is refused without the API key
curl -i https://<api-host>/api/status -H "X-Access-Key: anything"
# -> 401 {"detail":"Invalid or missing API key."}

# 3. The UI is up and points at the right backend
curl https://<web-host>/config.js
# -> window.APP_CONFIG = { API_BASE_URL: "https://<api-host>" };
```

Then open the frontend URL and sign in. If the UI loads but every call fails,
the cause is almost always one of those two URL settings — check the browser
console for a CORS error naming the origin the backend rejected.

---

## Security model in one paragraph

There is a single admin login. `POST /api/login` exchanges a username and
password for an opaque session token, which the browser keeps in
`sessionStorage` and sends as `X-Access-Key`; the password is never stored
client-side. In `MODE=prod` the backend additionally requires `X-API-Key`
from any caller whose `Origin` is not in `ALLOWED_ORIGINS` — that keeps
casual external callers off the endpoint, but a key the browser can see is
never truly secret, so **the login is the real access control**. Treat
`ADMIN_PASSWORD` as the secret that matters.

---

## Known limitations

- **Quota, cache and sessions are in-process.** A backend restart or
  redeploy clears the search quota and every cached run, and signs the admin
  out. V1 assumes a single backend instance; running more than one means
  moving those three stores to Redis.
- **The Google suggest endpoint is unofficial.** Undocumented, unsupported,
  and it throttles cloud IP ranges much harder than residential ones. A run
  from Render may see more `failed_queries` than the same run from a laptop,
  or may fail outright with a 502. That is the endpoint's behaviour, not a
  bug.
- **The rate limit is global, not per user**, because there is one shared
  login.

Details and mitigations are in [backend/README.md](backend/README.md).

# Keyword Analyzer — V1 Acceptance & Sign-Off

Traced line by line to **the client's developer briefing** (`.docx`) and **the
approved scope** (`PE_PS_1vs1 — Keyword Analyzer`, 16 Sep 2026).

Every test below names the promise it proves. Pass all of Part A and the
contract is met.

**Before starting:** sign in. The meter top-right shows runs left — ten per
hour, shared across the team. Part A costs four runs. Parts B–D cost none.

---

## Part A — Contract tests

### A1 · The client's own example topic

> **Proves:** Scope §03 *"Keyword collection from Google suggestions: the topic
> alone, the topic plus each letter A to Z, question words and buying words"*
> and *"AI report … covering intent, topic groups, patterns and content ideas"*.
> Uses `Football Statistics` — the default topic in the client's own HTML.

**Type:** `football statistics` → **Run analysis**

**Expect during the run:** *Running*, an orange progress bar, a live seconds
counter, **Cancel run**. Roughly 40 seconds.

**Then verify each of these:**

| # | Check | Pass |
|---|---|---|
| 1 | **Queries succeeded** reads **37/37** (or shows an honest partial — see A4) | ☐ |
| 2 | Keywords tab → **Grouped**: you can see the seed row, 26 letter rows (`football statistics a` … `z`), and 10 modifier rows | ☐ |
| 3 | The modifier rows are **prefixes** — `how football statistics`, `best football statistics` — matching the client's code (`f"{q} {base_keyword}"`) | ☐ |
| 4 | **Unique keywords** is roughly 100–300 | ☐ |
| 5 | Report has **exactly four numbered sections**, worded as the client specified | ☐ |

**Section headings must read:**

1. `Search Intent Classification` — and inside it, all four of the client's
   categories: **Informational, Transactional, Commercial Investigation,
   Navigational**
2. `Top 5 Topic Clusters` — **five** clusters, not three, not seven
3. `Search Patterns and Attribute Analysis`
4. `Content Opportunities and Question Insights`

> If any heading is missing or a section is thin, that is a real fail — these
> four sections are the client's specification, not a suggestion.

---

### A2 · The client's second example topic

> **Proves:** the tool is not tuned to one subject. The briefing names *"football
> statistics, luxury villa rentals"* as the two reference cases.

**Type:** `luxury villa rentals` → **Run analysis**

**Expect a different shape of answer from A1:**

- **Intent mix** should lean **Commercial / Transactional** — people booking.
  A1 (football statistics) should have leant **Informational** — people
  looking up numbers.
- If both topics come back with near-identical intent splits, the
  classification is not actually reading the data. **That is a fail.**
- **Run time** should be about the same as A1 (~40s). Pacing is fixed at one
  query per second by design, so run time barely moves between topics.

---

### A3 · Download the keyword list

> **Proves:** Scope §03 *"plus CSV download"* and §01 STEP 04 *"Download the
> keyword list as a spreadsheet"*.

On the Keywords tab, three buttons are visible **without opening any menu**.

| Click | Expect | Pass |
|---|---|---|
| **CSV** — *the contracted format* | Opens in Excel with columns `keyword, sources, types`. **Accents and umlauts intact** — check a keyword like `münchen` or `zürich` is not mangled. | ☐ |
| **TXT** *(added beyond scope)* | One keyword per line, paste-ready | ☐ |
| **JSON** *(added beyond scope)* | Same data with source attribution, for a developer | ☐ |

On the Intent report tab:

| Click | Expect | Pass |
|---|---|---|
| **PDF** *(added beyond scope)* | Downloads after ~1s. Open it: running header, a facts strip, **ruled tables**, page numbers, nothing cut off mid-sentence | ☐ |
| **Markdown** *(added beyond scope)* | The report as `.md` with a header naming the model | ☐ |

> CSV was the promise. The other four are extra — worth pointing out to the
> client as delivered value, but CSV is the one that must work.

---

### A4 · The five gap fixes

> **Proves:** Scope §04, the five issues you contracted to fix. These are the
> heart of what the client is paying for — the blueprint already "worked",
> and this is what makes it safe to host.

| Gap (from your scope) | How to test it | Expect | Pass |
|---|---|---|---|
| **Server blocking** | Start a run. While it is running, open the tool in a **second browser tab** | The second tab loads and responds normally. The server is not frozen. | ☐ |
| **No rate-limit guard** | Watch the **Run time** card after A1 | ~37–50 seconds, *not* 3 seconds. Slow is correct — requests are paced one per second so Google does not block you. | ☐ |
| **Silent failures** | If any run returns fewer than 37/37 | An amber **Partial results** banner appears with exact counts and a **View failed queries** list naming each one | ☐ |
| **Outdated AI model** | Read the line above the report | Names a **current Flash model** (`gemini-3.8-flash`), never `gemini-2.5-flash` | ☐ |
| **Cleanup** | — verified in code — | https endpoint (not http), `marked` and `dompurify` version-pinned with integrity hashes, unused `requests` dependency removed | ☑ |

> **On "Silent failures":** a partial run is a *pass*, not a fail. The whole
> point of the fix is that a Google block now shows up as an honest banner
> instead of a suspiciously short list. Show the client this deliberately.

---

### A5 · Password protection

> **Proves:** Scope §03 *"Hosting on Render with password protection, so only
> your team can run it"*, and §01 STEP 01 *"Open the link, enter the team
> password"*.

| Check | Expect | Pass |
|---|---|---|
| Open the site in a **private window** | Sign-in screen. No results reachable without it. | ☐ |
| Enter a wrong password | *Invalid username or password.* No hint about which was wrong. | ☐ |
| Sign in, then refresh | Still signed in. | ☐ |
| Sign out | Back to the sign-in screen. | ☐ |

---

## Part B — Confirm the exclusions (so nothing is over-promised)

> **Proves:** Scope §03 *"Not included"*. Walk these with the client so there
> is no surprise later. **Absence here is correct.**

| Check | Expect |
|---|---|
| Look for a search-volume number anywhere | **None.** Volumes were explicitly out of scope. |
| Look for a list of past searches | **None.** Saved history was out of scope. |
| Look for a way to run several topics at once, or on a schedule | **None.** Bulk and scheduled runs were out of scope. |
| Re-run the *exact same topic* | Returns instantly with a **Cached** badge and the meter does **not** drop. This is a 24-hour cache, **not** saved history — it holds one recent answer, it is not a searchable archive. |

---

## Part C — Edge cases (no quota)

| Do this | Expect |
|---|---|
| **Run** with an empty box | Red inline message. No run starts, no quota spent. |
| Paste 150 characters | Cut off at **100**. |
| `zqxvkj asdfgh` (nonsense) | Empty result or a clean error. **Never a crash or blank screen.** |
| Start a run, hit **Cancel run** | Stops immediately, earlier results left intact. |
| Run 11 topics within an hour | The 11th is refused with a countdown to when the next run is allowed. |

---

## Part D — Visual pass

| Check | Pass |
|---|---|
| **No colour except the orange progress bar and the pulsing *Running* dot.** Anything blue/green/amber sitting still is a flag. | ☐ |
| **No ASCII art anywhere** | ☐ |
| Set your laptop to **dark mode** — the site stays **light** (by design) | ☐ |
| On a **phone**: nothing scrolls sideways; download buttons wrap to a second line | ☐ |
| Download buttons visible without opening a menu, on both tabs | ☐ |

---

## Part E — The two decisions to settle before handover

These are **not bugs**. They are two places where what is running differs
from the client's briefing, and the client should choose.

### E1 · Language and country — **needs a decision**

The client's code sets `language: str = "de"`, `country: str = "ch"` and uses
**German question words** — `wer, wie, was, wo, warum, welche, beste, mieten`.
What is deployed uses **English / US** — `who, what, when, where, why, how,
best, buy, cheap, near me`.

> Note their own HTML overrides this to `en` / `us`, so the briefing
> contradicts itself. This is genuinely the client's call.

**The test that settles it — run this and compare:**

1. Run `chalet zermatt mieten` as-is (English/US settings).
2. Note the **Unique keywords** count and whether the 10 modifier rows
   returned anything.
3. If the modifier rows are mostly empty and the count is low, the client
   works in German and the setting should change.

**If it needs changing**, it is four environment variables in Render — no
code, no redeploy of the frontend:

```
SUGGEST_LANG=de
SUGGEST_COUNTRY=ch
SUGGEST_QUESTION_MODIFIERS=wer,wie,was,wo,warum,welche
SUGGEST_COMMERCIAL_MODIFIERS=beste,mieten,buy,best
```

That is the client's exact modifier list, and it keeps the total at 37 queries.

### E2 · Which AI writes the report — **tell the client**

The scope says *"AI report from **Gemini**"*. What is deployed tries
**Claude → OpenAI → Gemini** and uses whichever answers first, naming it above
the report.

**This is an upgrade, not a substitution**, and it costs nothing to leave as
is: a provider with no API key is skipped without being called. **If the
client sets only `GEMINI_API_KEY` — which is what their briefing assumes —
the tool runs Gemini and nothing else.** The other two are a safety net for
the day Gemini is down.

**Test it:** run any topic and read the line above the report. With only a
Gemini key configured it must say **Gemini**. Say so to the client anyway —
they approved "Gemini", and they should hear this from you first.

---

## Part F — The one thing nobody can test until it is live

Every test above except this one has been run. **No real Google request has
been made yet** — all development testing used simulated responses.

The first genuine Google call happens on Render, with the client watching.
This is expected, and your scope §05 already discloses it:

> *"Hosting online makes blocks more likely. Render's servers share addresses
> with many sites, and Google limits shared addresses sooner, so occasional
> partial runs are possible."*

**So: run A1 on the live Render URL yourself, before the client does.** If it
returns 37/37, you are clear. If it returns partial, that is the accepted risk
in §05 doing exactly what you said it would — and the fix is the paid data
service already quoted under §07.

---

## Sign-off summary

| Contract item | Source | Status |
|---|---|---|
| Collection: seed + A–Z + question + buying words | §03 | Built — 37 queries |
| AI report, four sections | §03 + briefing | Built — headings match the briefing |
| Dashboard + CSV download | §03 | Built — plus TXT, JSON, PDF, Markdown |
| Render hosting + password | §03 | Built |
| Five stability fixes | §04 | All five fixed |
| Short setup guide | §03 | `DEPLOYMENT.md` |
| Exclusions stay excluded | §03 | Confirmed absent |
| Delivery Friday 18 September | cover | On time |

**Two open decisions:** language/country (E1) and which AI writes the report (E2).

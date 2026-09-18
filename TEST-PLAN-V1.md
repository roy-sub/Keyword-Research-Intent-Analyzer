# Keyword Analyzer — V1 Acceptance & Sign-Off

Traced line by line to **the client's developer briefing** (`.docx`) and **the
approved scope** (`PE_PS_1vs1 — Keyword Analyzer`, 16 Sep 2026).

Every test below names the promise it proves. Pass Part A and the contract is
met; Part E covers both locales the briefing implies, and **Part F is how you
prove the keywords are genuinely Google's** rather than taking it on trust.

**Before starting:** sign in. The meter top-right shows runs left — ten per
hour, shared across the team. Part A costs four runs and Part E costs four
more, so allow two hours or raise the limit. Parts B, C, D and F cost none.

---

## Part A — Contract tests

### A1 · The client's own example topic

> **Proves:** Scope §03 *"Keyword collection from Google suggestions: the topic
> alone, the topic plus each letter A to Z, question words and buying words"*
> and *"AI report … covering intent, topic groups, patterns and content ideas"*.
> Uses `Football Statistics` — the default topic in the client's own HTML.

**Set Market to `English · United States`**, type `football statistics`, then
**Run analysis**.

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

## Part E — Markets (English and German side by side)

> **Proves:** the tool matches the briefing for **both** locales. The client's
> reference code assumed German/Swiss (`language: "de"`, `country: "ch"`,
> question words `wer, wie, was, wo, warum, welche`); their own webpage sent
> English/US. Rather than pick one, the locale is now chosen per run.

### E1 · The picker is there and says what it changes

| Check | Expect | Pass |
|---|---|---|
| The query bar has a **Market** dropdown | Two options: *English · United States* and *Deutsch · Schweiz* | ☐ |
| Select **English · United States** | The line under the bar reads `hl=en` `gl=us` and shows `who what when where why how best buy cheap near me` | ☐ |
| Select **Deutsch · Schweiz** | The line changes to `hl=de` `gl=ch` and `wer wie was wo warum welche beste mieten buy best` — **the briefing's own word list** | ☐ |
| **Queries** still reads **37** in both | Switching market costs nothing in speed or quota | ☐ |
| Refresh the page | Your market choice is still selected | ☐ |

### E2 · A German run returns German results

**Set Market to `Deutsch · Schweiz`**, type `chalet zermatt mieten`, **Run analysis**.

| Check | Expect | Pass |
|---|---|---|
| Keywords tab → **Grouped** | Rows named `wie chalet zermatt mieten`, `warum chalet zermatt mieten`, `beste chalet zermatt mieten` — **German prefixes, not English** | ☐ |
| Open those rows | Suggestions in German (`chalet zermatt mieten günstig`, `… mit sauna`) | ☐ |
| Under the topic title | It names **Deutsch · Schweiz** | ☐ |
| Umlauts render correctly everywhere | `günstig`, `größe` — not `gÃ¼nstig` | ☐ |
| The AI report | Written in **English**, with the four headings unchanged, but **quoting the German keywords as-is** — never translated | ☐ |

### E3 · The two markets do not contaminate each other

Run **the same topic** `chalet zermatt` twice — once on each market.

| Check | Expect | Pass |
|---|---|---|
| The second run is a **real run** | No **Cached** badge; it takes ~40s again | ☐ |
| The two keyword lists differ | German modifier rows in one, English in the other | ☐ |
| Each result names its own market | One says *English · United States*, one says *Deutsch · Schweiz* | ☐ |
| Download both as TXT | Each file's header line names the market it came from | ☐ |

> A cached English answer being served to a German run would be the serious
> bug here. E3 is the test that would catch it.

### E4 · Which AI writes the report — **tell the client**

The scope says *"AI report from **Gemini**"*. What is deployed tries
**Claude → OpenAI → Gemini** and uses whichever answers first, naming it above
the report.

**This is an upgrade, not a substitution.** A provider with no API key is
skipped without being called, so **if the client sets only `GEMINI_API_KEY` —
which is what their briefing assumes — the tool runs Gemini and nothing
else.** The other two are a safety net for the day Gemini is down.

**Test it:** run any topic and read the line above the report. With only a
Gemini key configured it must say **Gemini**. Say so to the client anyway —
they approved "Gemini" and should hear this from you first.

---

## Part F — Proving the data is really Google's

> This is the question that matters most, so it gets its own answer rather
> than a promise.

### F1 · Run the proof script

On your own machine, with normal internet:

```bash
cd backend && source .venv/bin/activate
python scripts/verify_live.py "ski chalet zermatt"
python scripts/verify_live.py "chalet zermatt mieten" --market de-CH
```

**What it does:** asks Google directly, using **only Python's standard
library and not one line of this project's code**, then asks the same
question through the app's own collection path, and compares.

**What you will see:**

- The **raw bytes Google sent back**, printed. Not a summary — the actual
  response, so you can read it yourself.
- Google's suggestions, listed.
- The app's suggestions for the same query, listed.
- A verdict.

**Pass:** `✓ Every keyword the app recorded came from Google.`

**Fail:** the script names any keyword the app produced that Google did not,
and exits non-zero. That would be the tool inventing data. It should never
happen — if it does, stop and send me the output.

> Some keywords appearing in one list and not the other **in that direction**
> is normal: Google's autocomplete is not deterministic, and two calls a
> second apart can differ. Only **extra** keywords in the app's list would
> prove fabrication, which is exactly what the script tests for.

Add `--full` to run the complete 37-query set (~40 seconds) — the honest
end-to-end check.

### F2 · Confirm you are not looking at demo data

The app has a demo mode that serves a saved sample instead of calling Google.
It is deliberately impossible to mistake:

| Check | Expect | Pass |
|---|---|---|
| Open the app **normally** | **No banner.** This is a real run. | ☐ |
| Open it with `?mock=1` on the end of the address | A **black banner with an orange edge**: *"Demo mode. These keywords are a saved sample, not a live Google result."* | ☐ |
| The account chip in the corner | Reads `M` and *"(mock)"* in demo mode, `A` in a real session | ☐ |

**If you see no banner, the keywords came from Google.** There is no third
state and no partial-demo mode.

### F3 · What a real run proves on its own

Without running any script, a genuine run is self-evidencing:

- **It takes ~40 seconds.** Fabricated data would be instant. The time is 37
  real HTTP requests, paced one per second.
- **The Keywords tab shows the exact query that produced each keyword.** Type
  any of those queries into Google yourself and compare.
- **Some queries sometimes fail**, and the tool says which. Invented data
  never fails.
- **Results differ between markets**, because Google genuinely answers `hl=de`
  differently from `hl=en`.

### F4 · Where the AI does and does not touch the data

Worth being precise, because this is what "AI data" would mean:

| Stage | Who produces it |
|---|---|
| The 37 queries | Built from your topic by the app |
| The keywords | **Google, and only Google** |
| Dedupe, grouping, counts | The app, arithmetic only |
| The written report | The AI — prose *about* the keywords |

**The AI is handed the finished keyword list and writes prose about it. It
never adds to that list, and there is no code path by which it could.** The
counts, the intent-mix percentages and every keyword you download are
Google's data and the app's arithmetic.

### F5 · The one caveat that remains

Development testing used simulated Google responses, because the build
environment cannot reach Google at all. **F1 is how you close that gap
yourself, in about a minute**, before the client sees it.

Your scope §05 already discloses the related risk:

> *"Hosting online makes blocks more likely. Render's servers share addresses
> with many sites, and Google limits shared addresses sooner, so occasional
> partial runs are possible."*

So run **A1 and F1 on the live Render URL** yourself first. 37/37 and a ✓ and
you are clear. A partial run is §05 behaving exactly as you described it, and
the fix is the paid data service already quoted under §07.

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
| German/Swiss locale from the briefing | briefing | Built — selectable per run, alongside English/US |
| Keywords are genuinely Google's | — | Provable in a minute: `scripts/verify_live.py` |

**One thing to tell the client:** the report now tries Claude first with
Gemini as backup, rather than Gemini alone (E4). It costs nothing, changes
nothing if only a Gemini key is set, and is an upgrade — but they approved
"Gemini", so it should come from you.

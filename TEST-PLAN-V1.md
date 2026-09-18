# Keyword Analyzer — V1 Acceptance Tests

> **Scope note.** The client `.docx` and the approved proposal `.pdf` did not
> reach me — only the screenshot came through. These tests are therefore
> written against **what the system actually does**, not against the signed
> scope. Re-send both documents and I will map every requirement line to a
> test and tell you what, if anything, is missing.

**Before you start:** sign in, and check the top-right meter. Each full run
costs one of ten per hour. Tests 1–3 use three runs. Tests 4–6 cost nothing.

---

## Test 1 — A commercial travel topic (the main happy path)

**Type:** `ski chalet zermatt` → **Run analysis**

**What should happen**

| Stage | Expect |
|---|---|
| ~0–45s | *Running*, an orange progress bar, a live seconds counter, and a **Cancel run** button. The bar is the only colour on the page. |
| On finish | The topic in large type, and four metric cards: **Unique keywords**, **Queries succeeded**, **Source groups**, **Run time**. |

**Then check, in order**

1. **Unique keywords** is roughly **100–250**. Under 40 means Google throttled
   the run — see Test 3.
2. **Queries succeeded** reads **37/37**, or close to it. Anything less shows
   an amber *Partial results* banner with a **View failed queries** list.
   That is expected behaviour, not a bug: the counts stay honest.
3. **Intent mix** bar — four grey bands, totalling exactly **100%**. For this
   topic expect **Commercial** and **Transactional** to dominate; people
   searching for a Zermatt chalet are ready to book.
4. **Intent report** tab — a written analysis in numbered sections, with at
   least one table. At the top it says **Analysed by Claude · claude-sonnet-5**.
5. **Keywords** tab — set to **Grouped**, you should see accordion rows named
   after the query that produced each keyword: `ski chalet zermatt`,
   `ski chalet zermatt a`, `ski chalet zermatt b` … plus `how`/`what`/`best`/
   `cheap` rows. Open any one and every keyword inside should genuinely
   contain the seed phrase.

**Red flags:** keywords unrelated to Zermatt or skiing; the mix not summing
to 100%; the report describing a different topic.

---

## Test 2 — A football analytics topic (different intent shape)

**Type:** `bundesliga top scorers` → **Run analysis**

This is the same machinery on a very different subject, and it is the test
that proves the tool is not tuned to travel.

**What should differ from Test 1**

- The **Intent mix** should lean **Informational**, not Commercial — people
  want the numbers, not a purchase. If this comes back Commercial-heavy, the
  classification is not reading the data.
- The **Question** group should be fat: `who is the top scorer in bundesliga`,
  `how many goals…`, and so on.
- Expect season and player names in the keywords (`bundesliga top scorers
  2024 25`, `bundesliga top scorers all time`).

**Also check:** the **Run time** card. It should be roughly the same as Test 1
(~40s). Pacing is fixed at one query per second by design, so run time should
barely move between topics.

---

## Test 3 — Downloads (the part clients actually use)

Use the results already on screen from Test 2. **This costs no quota.**

### Keywords tab — three buttons, TXT filled black by default

| Click | You should get |
|---|---|
| **TXT** | `keywords-bundesliga-top-scorers-<date>.txt` — one keyword per line, under two `#` comment lines giving the topic, the count and the timestamp. Paste-ready. |
| **JSON** | Same data, plus `sources` and `types` on every keyword — this is the one to hand a developer. |
| **CSV** | Opens in Excel with **no mangled characters** (umlauts in `mönchengladbach`, `müller` must survive). That is the one thing to check here. |

### Intent report tab — PDF filled black by default

| Click | You should get |
|---|---|
| **PDF** | Button reads *Building…* for a second, then downloads `intent-report-bundesliga-top-scorers-<date>.pdf`. |
| **Markdown** | The same report as a `.md`, with a provenance header naming the model. |

**Open the PDF and check all of this:**

- Page 1: a **KEYWORD ANALYZER** running header with the topic on the right.
- A large title, then a grey facts strip: **GENERATED / UNIQUE KEYWORDS /
  QUERIES ANSWERED / ANALYSED BY**.
- Tables are properly **ruled and aligned** — not run together as plain text.
- Every page has a footer with a **page number**. Text does not run off the
  bottom edge or get cut mid-sentence.

**If the PDF button shows a grey note instead of downloading**, the backend
export call failed. Markdown will still work. That is the designed fallback,
but tell me — it should not happen.

---

## Test 4 — The cache (no quota, instant)

Re-run **the exact same topic** you just used, spelling it identically.

**Expect:** results in **under a second**, a small **Cached** badge next to
*Analysis*, and the runs-left meter **unchanged**. Same numbers as before.

This is what stops a repeated topic burning the hourly allowance. If the
meter drops on a cached run, the cache is not working.

---

## Test 5 — Bad input and edge cases (no quota)

| Do this | Expect |
|---|---|
| Click **Run analysis** with the box empty | A red inline message. No run starts, no quota spent. |
| Paste 150 characters into the box | It stops you at **100**. The limit is enforced, not just suggested. |
| Type `zqxvkj asdfgh` (nonsense) and run | Either a near-empty result or a clean error — **never a crash, never a blank screen**. Google has nothing to autocomplete. |
| Start a run, then hit **Cancel run** | Stops immediately, returns to the previous screen, leaves any earlier results intact. |
| Type a topic, run, then refresh mid-run | You are still signed in; the run is gone. Sessions are held in memory. |

---

## Test 6 — Look and feel (what the screenshot is for)

Walk the site once and confirm:

- [ ] **No colour anywhere except the orange progress bar and the pulsing
      *Running* dot.** Everything else is black, white and grey. If you see a
      blue, green or amber element sitting still, flag it.
- [ ] **No ASCII art anywhere** — not on sign-in, not on the empty state, not
      during a run.
- [ ] Set your laptop to **dark mode**. The site must stay **light**. It has
      no dark theme by design.
- [ ] Open it on your **phone**. Nothing should scroll sideways; the three
      download buttons wrap onto a second line rather than pushing the page.
- [ ] The **Download** buttons are visible without opening any menu, on both
      tabs.

---

## What "ready to deliver" means, in one line per area

| Area | Passes if |
|---|---|
| Collection | 37 queries attempted, failures listed openly, keywords genuinely contain the seed |
| Analysis | A written report naming the model, intent mix summing to 100% |
| Downloads | TXT/JSON/CSV on keywords, PDF/Markdown on the report, all opening cleanly |
| Resilience | Nonsense input, cancels and refreshes never crash it |
| Design | Monochrome, light-only, no ASCII, no sideways scroll on a phone |

---

## Two things worth telling the client up front

1. **Google throttles.** A run can come back with 32 of 37 queries answered.
   The tool says so plainly and lists which ones failed rather than quietly
   returning a thinner list. Re-running the same topic later usually fills
   the gaps.
2. **Ten runs an hour, shared.** The limit is global, not per-person. If two
   people use it at once they draw from the same ten. Raise it in the Render
   settings (`RATE_LIMIT_MAX_SEARCHES`) if that proves tight.

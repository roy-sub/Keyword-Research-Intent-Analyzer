/* ==========================================================================
   REFRACT — application logic

   The backend is a separate service on its own origin. Its base URL comes
   from window.APP_CONFIG.API_BASE_URL in config.js, which build.sh rewrites
   at deploy time; an empty value means "same origin as this page".

   Add ?mock=1 to the URL to bypass the backend entirely and exercise the
   whole UI against mock-response.json — mock and real runs both end in the
   same rendering path, so there is exactly one way results are drawn.
   ========================================================================== */

(function () {
  "use strict";

  /* ------------------------------------------------------------------
     CONSTANTS + STATE
     ------------------------------------------------------------------ */

  /* Holds the opaque session token returned by POST /api/login. The
     password itself is never stored. */
  const ACCESS_KEY_STORAGE = "refract.session";
  const CLIENT_TIMEOUT_MS = 180000;
  const MOCK_DELAY_MS = 6000;

  /* Per-query wall-clock overhead on top of the configured pacing delay,
     measured against real runs. Used only to estimate progress. */
  const PER_QUERY_OVERHEAD_S = 0.15;
  const FALLBACK_QUERIES = 37;
  const FALLBACK_DELAY_S = 1.0;

  const TYPE_ORDER = ["seed", "alphabet", "question", "commercial"];

  const state = {
    accessKey: null,
    mockMode: false,
    mockSearchesRemaining: 6,
    status: null,
    quotaMax: 0,
    lastResult: null,
    activeTab: "report",
    rawMode: "grouped",
    filterText: "",
    abortController: null,
    cancelledByUser: false,
    timedOut: false,
    formHeldDisabled: false,
    runTimerId: null,
    timeoutId: null,
    elapsedSeconds: 0,
    openMenu: null
  };

  /* Backend origin, without a trailing slash. Empty means same-origin. */
  const API_BASE_URL = resolveApiBaseUrl();

  function resolveApiBaseUrl() {
    const configured = String(
      (window.APP_CONFIG && window.APP_CONFIG.API_BASE_URL) || ""
    ).replace(/\/+$/, "");
    if (!configured) return "";

    /* Local-development convenience. config.js ships pointing at
       http://localhost:8000, but the page itself may be served from a
       different host — http://0.0.0.0:5173, or a LAN IP when testing from a
       phone. A loopback address in that situation means "the machine running
       the browser", which is the wrong machine. Re-point it at whatever host
       served this page, keeping the backend's port.

       Only loopback hostnames are rewritten, so a real deployment (where
       API_BASE_URL is a remote host) is never touched. */
    try {
      const url = new URL(configured, location.href);
      const isLoopback = url.hostname === "localhost" || url.hostname === "127.0.0.1";
      if (isLoopback && location.hostname && location.hostname !== url.hostname) {
        url.hostname = location.hostname;
        return url.origin;
      }
    } catch (e) {
      /* Not parseable as a URL — use it as configured. */
    }
    return configured;
  }

  /* fetch() rejects with the same opaque TypeError whether the server is
     down or the browser blocked the response for CORS, and the UI can only
     say "could not reach the server". The console can say more. */
  function logConnectionDiagnostics(err) {
    const target = API_BASE_URL || location.origin;
    console.error(
      "[refract] Could not reach the API at " + target + ".\n" +
        "  1. Is the backend running and listening there?\n" +
        "  2. Does the backend allow this page's origin (" + location.origin +
        ")? In MODE=prod that means ALLOWED_ORIGINS must contain it exactly.\n" +
        "  3. Is API_BASE_URL in config.js correct?\n" +
        "A CORS rejection shows up in the Network tab as a failed OPTIONS " +
        "preflight, often with status 400.",
      err
    );
  }

  /* ------------------------------------------------------------------
     DOM
     ------------------------------------------------------------------ */

  const $ = (id) => document.getElementById(id);

  const el = {
    libError: $("lib-load-error"),

    gateScreen: $("gate-screen"),
    gateForm: $("gate-form"),
    gateUsername: $("gate-username"),
    gatePassword: $("gate-password"),
    gateError: $("gate-error"),
    gateErrorText: $("gate-error-text"),
    gateSubmit: $("gate-submit"),

    app: $("app"),
    quotaFill: $("quota-fill"),
    quotaLine: $("quota-line"),
    accountBtn: $("account-btn"),
    accountMenu: $("account-menu"),
    accountInitial: $("account-initial"),
    accountName: $("account-name"),
    logoutBtn: $("logout-btn"),

    topicForm: $("topic-form"),
    topicInput: $("topic-input"),
    topicError: $("topic-error"),
    topicErrorText: $("topic-error-text"),
    runBtn: $("run-btn"),
    metaQueries: $("meta-queries"),
    metaPacing: $("meta-pacing"),

    statusRegion: $("status-region"),
    runNote: $("run-note"),
    runningStatus: $("running-status"),
    runningMessage: $("running-message"),
    elapsedValue: $("elapsed-value"),
    runProgress: $("run-progress"),
    cancelBtn: $("cancel-btn"),

    errorBanner: $("error-banner"),
    errorTitle: $("error-title"),
    errorMessage: $("error-message"),
    errorCountdown: $("error-countdown"),
    countdownFill: $("countdown-fill"),
    countdownValue: $("countdown-value"),
    errorActionBtn: $("error-action-btn"),

    emptyState: $("empty-state"),

    results: $("results"),
    cachedBadge: $("cached-badge"),
    resultsTopic: $("results-topic"),
    generatedAt: $("generated-at"),
    exportBtn: $("export-btn"),
    exportMenu: $("export-menu"),
    downloadReportBtn: $("download-report-btn"),
    downloadCsvBtn: $("download-csv-btn"),

    statTotal: $("stat-total"),
    statTotalSub: $("stat-total-sub"),
    statQueries: $("stat-queries"),
    queriesFill: $("queries-fill"),
    statSources: $("stat-sources"),
    statDuration: $("stat-duration"),
    statDurationSub: $("stat-duration-sub"),

    mixBar: $("mix-bar"),
    mixLegend: $("mix-legend"),

    partialBanner: $("partial-banner"),
    partialTitle: $("partial-title"),
    failedQueriesList: $("failed-queries-list"),

    tabReport: $("tab-report"),
    tabRaw: $("tab-raw"),
    tabRawCount: $("tab-raw-count"),
    panelReport: $("panel-report"),
    panelRaw: $("panel-raw"),

    copyReportBtn: $("copy-report-btn"),
    reportBody: $("report-body"),
    reportNav: $("report-nav"),
    reportToc: $("report-toc"),

    rawFilter: $("raw-filter"),
    modeGrouped: $("mode-grouped"),
    modeFlat: $("mode-flat"),
    copyAllBtn: $("copy-all-btn"),
    filterSummary: $("filter-summary"),
    groupView: $("group-view"),
    flatView: $("flat-view")
  };

  const exampleChips = Array.prototype.slice.call(document.querySelectorAll(".chip-btn"));

  /* ------------------------------------------------------------------
     UTILS
     ------------------------------------------------------------------ */

  function slugify(text) {
    const s = text.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-+|-+$)/g, "");
    return s || "topic";
  }

  function todayIso(fromDate) {
    const d = fromDate ? new Date(fromDate) : new Date();
    if (isNaN(d.getTime())) return new Date().toISOString().slice(0, 10);
    return d.toISOString().slice(0, 10);
  }

  function formatTimestamp(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "";
    return d.toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit"
    });
  }

  function csvField(value) {
    const s = String(value == null ? "" : value);
    if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  function debounce(fn, ms) {
    let handle = null;
    return function debounced(...args) {
      clearTimeout(handle);
      handle = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  function downloadTextFile(filename, mime, content) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        return true;
      } catch (e2) {
        return false;
      }
    }
  }

  async function safeJson(response) {
    try { return await response.json(); } catch (e) { return null; }
  }

  function librariesAvailable() {
    return !window.__libLoadFailed && typeof window.marked !== "undefined" && typeof window.DOMPurify !== "undefined";
  }

  function announce(message) {
    el.filterSummary.textContent = message;
  }

  /* Build an <svg class="icon"> referencing the sprite. Markup must carry
     the viewBox — CSS cannot set one. */
  function icon(name, extraClass) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon" + (extraClass ? " " + extraClass : ""));
    svg.setAttribute("viewBox", "0 0 20 20");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function setButtonIcon(button, name) {
    const use = button.querySelector(".btn__icon use");
    if (use) use.setAttribute("href", "#i-" + name);
  }

  /* Transient success/failure feedback on an action button. */
  function flashButton(button, ok, doneLabel, failLabel) {
    const label = button.querySelector(".btn__label");
    const original = label ? label.textContent : "";
    if (label) label.textContent = ok ? doneLabel : failLabel;
    setButtonIcon(button, ok ? "check" : "close");
    button.classList.toggle("is-done", ok);
    setTimeout(() => {
      if (label) label.textContent = original;
      setButtonIcon(button, "copy");
      button.classList.remove("is-done");
    }, 1600);
  }

  /* ------------------------------------------------------------------
     API
     ------------------------------------------------------------------ */

  /* Every API path goes through here, so there is one place that knows
     where the backend lives. */
  function apiUrl(path) {
    return API_BASE_URL + path;
  }

  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.detail) || "Request failed");
      this.status = status;
      this.body = body || {};
    }
  }

  async function apiLogin(username, password) {
    const res = await fetch(apiUrl("/api/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    if (!res.ok) throw new ApiError(res.status, await safeJson(res));
    return safeJson(res);
  }

  async function apiStatus() {
    const res = await fetch(apiUrl("/api/status"), {
      headers: { "X-Access-Key": state.accessKey }
    });
    if (!res.ok) throw new ApiError(res.status, await safeJson(res));
    return res.json();
  }

  async function apiAnalyze(topic, signal) {
    const res = await fetch(apiUrl("/api/analyze"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Access-Key": state.accessKey },
      body: JSON.stringify({ topic }),
      signal
    });
    if (!res.ok) throw new ApiError(res.status, await safeJson(res));
    return res.json();
  }

  async function apiLogout(key) {
    return fetch(apiUrl("/api/logout"), {
      method: "POST",
      headers: { "X-Access-Key": key }
    });
  }

  async function mockAnalyze(topic, signal) {
    await mockDelay(MOCK_DELAY_MS, signal);
    const res = await fetch("mock-response.json", { signal });
    if (!res.ok) throw new Error("Could not load mock-response.json");
    const data = await res.json();
    data.topic = topic;
    data.searches_remaining = Math.max(0, state.mockSearchesRemaining - 1);
    state.mockSearchesRemaining = data.searches_remaining;
    return data;
  }

  function mockDelay(ms, signal) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      }
    });
  }

  /* ------------------------------------------------------------------
     MENUS
     ------------------------------------------------------------------ */

  function openMenu(button, panel) {
    closeMenu();
    panel.classList.remove("is-hidden");
    button.setAttribute("aria-expanded", "true");
    state.openMenu = { button, panel };
  }

  function closeMenu() {
    if (!state.openMenu) return;
    state.openMenu.panel.classList.add("is-hidden");
    state.openMenu.button.setAttribute("aria-expanded", "false");
    state.openMenu = null;
  }

  function toggleMenu(button, panel) {
    if (state.openMenu && state.openMenu.panel === panel) closeMenu();
    else openMenu(button, panel);
  }

  document.addEventListener("click", (e) => {
    if (!state.openMenu) return;
    const { button, panel } = state.openMenu;
    if (!panel.contains(e.target) && !button.contains(e.target)) closeMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.openMenu) {
      const btn = state.openMenu.button;
      closeMenu();
      btn.focus();
    }
  });

  /* ------------------------------------------------------------------
     GATE / SHELL
     ------------------------------------------------------------------ */

  function showGate(message) {
    closeMenu();
    el.app.classList.add("is-hidden");
    el.gateScreen.classList.remove("is-hidden");
    el.gatePassword.value = "";
    if (message) showGateError(message);
    setTimeout(() => el.gateUsername.focus(), 0);
  }

  /* The stored access key is a server-issued session token, never the
     password. Any 401 means it is gone or expired, so it is cleared here and
     the user is sent back to the gate. */
  function clearSession() {
    try { sessionStorage.removeItem(ACCESS_KEY_STORAGE); } catch (e) { /* private mode */ }
    state.accessKey = null;
    state.status = null;
  }

  function showApp() {
    el.gateScreen.classList.add("is-hidden");
    el.app.classList.remove("is-hidden");
  }

  function showGateError(message) {
    el.gateErrorText.textContent = message;
    el.gateError.classList.remove("is-hidden");
    el.gateUsername.setAttribute("aria-invalid", "true");
    el.gatePassword.setAttribute("aria-invalid", "true");
  }

  function clearGateError() {
    el.gateError.classList.add("is-hidden");
    el.gateUsername.removeAttribute("aria-invalid");
    el.gatePassword.removeAttribute("aria-invalid");
  }

  function setIdentity(username) {
    el.accountName.textContent = username;
    el.accountInitial.textContent = (username || "?").charAt(0);
  }

  /* ------------------------------------------------------------------
     QUOTA + RUN PARAMETERS
     ------------------------------------------------------------------ */

  function renderQuotaLine() {
    const remaining = state.mockMode
      ? state.mockSearchesRemaining
      : (state.status ? state.status.searches_remaining : null);

    if (remaining === null || remaining === undefined) {
      el.quotaLine.textContent = "";
      el.quotaFill.style.width = "0%";
      return;
    }

    /* The API reports what is left, not the ceiling. The highest value seen
       this session is the best available stand-in, and it self-corrects
       upward as the window rolls over. */
    state.quotaMax = Math.max(state.quotaMax, remaining, 1);
    const pct = Math.round((remaining / state.quotaMax) * 100);

    el.quotaFill.style.width = pct + "%";
    el.quotaFill.classList.toggle("is-none", remaining === 0);
    el.quotaFill.classList.toggle("is-low", remaining > 0 && pct <= 30);
    el.quotaLine.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = String(remaining);
    el.quotaLine.appendChild(b);
    el.quotaLine.appendChild(
      document.createTextNode(
        " run" + (remaining === 1 ? "" : "s") + " left" + (state.mockMode ? " (mock)" : "")
      )
    );
  }

  function renderRunParameters() {
    const queries = expectedQueries();
    const delay = pacingDelay();
    el.metaQueries.textContent = String(queries);
    el.metaPacing.textContent = delay.toFixed(1) + "s";
  }

  function expectedQueries() {
    return (state.status && state.status.expected_queries) || FALLBACK_QUERIES;
  }

  function pacingDelay() {
    const d = state.status && state.status.request_delay_seconds;
    return typeof d === "number" ? d : FALLBACK_DELAY_S;
  }

  /* Estimated seconds for the collection phase. Used only to draw a
     progress bar — the API reports no incremental progress. */
  function collectionEstimate() {
    if (state.mockMode) return MOCK_DELAY_MS / 1000 * 0.75;
    return expectedQueries() * (pacingDelay() + PER_QUERY_OVERHEAD_S);
  }

  function setFormDisabled(disabled) {
    el.topicInput.disabled = disabled;
    el.runBtn.disabled = disabled;
    exampleChips.forEach((chip) => { chip.disabled = disabled; });
  }

  /* ------------------------------------------------------------------
     RUN STATUS
     ------------------------------------------------------------------ */

  function showRunning() {
    el.statusRegion.classList.remove("is-hidden");
    el.runningStatus.classList.remove("is-hidden");
    el.errorBanner.classList.add("is-hidden");
  }

  function hideRunning() {
    el.runningStatus.classList.add("is-hidden");
    el.runProgress.classList.remove("progress__fill--indeterminate");
    el.runProgress.style.width = "0%";
    if (el.errorBanner.classList.contains("is-hidden") && el.runNote.classList.contains("is-hidden")) {
      el.statusRegion.classList.add("is-hidden");
    }
  }

  function updateRunningMessage() {
    const estimate = collectionEstimate();
    const elapsed = state.elapsedSeconds;
    el.elapsedValue.textContent = String(elapsed);

    if (elapsed < estimate) {
      el.runningMessage.textContent = "Collecting autocomplete suggestions";
      el.runProgress.classList.remove("progress__fill--indeterminate");
      const pct = Math.min(88, Math.round((elapsed / estimate) * 88));
      el.runProgress.style.width = pct + "%";
      el.runProgress.parentElement.setAttribute("aria-valuenow", String(pct));
    } else {
      el.runningMessage.textContent = "Reading search intent";
      el.runProgress.classList.add("progress__fill--indeterminate");
      el.runProgress.parentElement.removeAttribute("aria-valuenow");
    }
  }

  let countdownIntervalId = null;

  function clearErrorCountdown() {
    if (countdownIntervalId) { clearInterval(countdownIntervalId); countdownIntervalId = null; }
  }

  function showError(options) {
    clearErrorCountdown();
    el.statusRegion.classList.remove("is-hidden");
    el.errorBanner.classList.remove("is-hidden");
    el.errorTitle.textContent = options.title;
    el.errorMessage.textContent = options.message;

    if (options.actionLabel) {
      el.errorActionBtn.textContent = options.actionLabel;
      el.errorActionBtn.classList.remove("is-hidden");
      el.errorActionBtn.onclick = options.onAction || null;
    } else {
      el.errorActionBtn.classList.add("is-hidden");
      el.errorActionBtn.onclick = null;
    }

    if (typeof options.countdownSeconds === "number" && options.countdownSeconds > 0) {
      el.errorCountdown.classList.remove("is-hidden");
      const total = options.countdownSeconds;
      let remaining = total;
      const tick = () => {
        el.countdownValue.textContent = formatCountdown(remaining);
        el.countdownFill.style.width = Math.round(((total - remaining) / total) * 100) + "%";
      };
      tick();
      countdownIntervalId = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
          clearErrorCountdown();
          clearError();
          if (options.onCountdownEnd) options.onCountdownEnd();
        } else {
          tick();
        }
      }, 1000);
    } else {
      el.errorCountdown.classList.add("is-hidden");
    }
  }

  function showNote(message) {
    el.statusRegion.classList.remove("is-hidden");
    el.runNote.textContent = message;
    el.runNote.classList.remove("is-hidden");
    setTimeout(() => {
      el.runNote.classList.add("is-hidden");
      if (el.runningStatus.classList.contains("is-hidden") && el.errorBanner.classList.contains("is-hidden")) {
        el.statusRegion.classList.add("is-hidden");
      }
    }, 3000);
  }

  function clearError() {
    clearErrorCountdown();
    el.errorBanner.classList.add("is-hidden");
    if (el.runningStatus.classList.contains("is-hidden") && el.runNote.classList.contains("is-hidden")) {
      el.statusRegion.classList.add("is-hidden");
    }
  }

  function formatCountdown(totalSeconds) {
    const s = Math.max(0, totalSeconds);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
  }

  /* ------------------------------------------------------------------
     RESULTS
     ------------------------------------------------------------------ */

  function renderResults(data) {
    state.lastResult = data;

    el.emptyState.classList.add("is-hidden");

    el.resultsTopic.textContent = data.topic;
    el.generatedAt.textContent = formatTimestamp(data.generated_at);
    el.cachedBadge.classList.toggle("is-hidden", !data.cached);

    renderMetrics(data);
    renderIntentMix(data);

    const hasFailed = Array.isArray(data.failed_queries) && data.failed_queries.length > 0;
    el.partialBanner.classList.toggle("is-hidden", !hasFailed);
    if (hasFailed) {
      el.partialTitle.textContent =
        `Partial results — ${data.queries_succeeded} of ${data.queries_attempted} queries answered`;
      el.failedQueriesList.innerHTML = "";
      const frag = document.createDocumentFragment();
      data.failed_queries.forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q;
        frag.appendChild(li);
      });
      el.failedQueriesList.appendChild(frag);
    }

    el.tabRawCount.textContent = String(data.total_keywords);
    renderReportTab(data.analysis_markdown);

    state.filterText = "";
    el.rawFilter.value = "";
    renderRawTab();

    el.results.classList.remove("is-hidden");
  }

  function renderMetrics(data) {
    el.statTotal.textContent = String(data.total_keywords);
    const perQuery = data.queries_succeeded
      ? (data.total_keywords / data.queries_succeeded).toFixed(1)
      : "0";
    el.statTotalSub.textContent = `${perQuery} per successful query`;

    el.statQueries.textContent = `${data.queries_succeeded}/${data.queries_attempted}`;
    const pct = data.queries_attempted
      ? Math.round((data.queries_succeeded / data.queries_attempted) * 100)
      : 0;
    el.queriesFill.style.width = pct + "%";
    el.queriesFill.classList.toggle("is-partial", pct < 100);

    el.statSources.textContent = String(data.sources.length);

    el.statDuration.textContent = `${Math.round(data.duration_seconds)}s`;
    el.statDurationSub.textContent = data.cached ? "from cache" : "live collection";
  }

  /* Each keyword is counted once, under the query type that first surfaced
     it — `types` preserves discovery order, so types[0] is that type. */
  function renderIntentMix(data) {
    const counts = {};
    TYPE_ORDER.forEach((t) => { counts[t] = 0; });
    data.keywords.forEach((k) => {
      const primary = (k.types && k.types[0]) || "alphabet";
      if (counts[primary] === undefined) counts[primary] = 0;
      counts[primary]++;
    });

    const total = data.keywords.length || 1;
    el.mixBar.innerHTML = "";
    el.mixLegend.innerHTML = "";

    const barFrag = document.createDocumentFragment();
    const legendFrag = document.createDocumentFragment();

    TYPE_ORDER.forEach((type) => {
      const n = counts[type] || 0;
      if (!n) return;
      const pct = (n / total) * 100;

      const slice = document.createElement("span");
      slice.className = `mix__slice mix__slice--${type}`;
      slice.style.width = pct + "%";
      slice.title = `${type}: ${n} keyword${n === 1 ? "" : "s"}`;
      barFrag.appendChild(slice);

      const li = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = `mix__swatch mix__slice--${type}`;
      const name = document.createElement("span");
      name.className = "mix__name";
      name.textContent = type;
      const value = document.createElement("span");
      value.className = "mix__pct";
      value.textContent = `${n} · ${pct.toFixed(0)}%`;
      li.appendChild(swatch);
      li.appendChild(name);
      li.appendChild(value);
      legendFrag.appendChild(li);
    });

    el.mixBar.appendChild(barFrag);
    el.mixLegend.appendChild(legendFrag);
  }

  let tocObserver = null;

  function renderReportTab(markdown) {
    if (!librariesAvailable()) {
      /* Safe degradation: plain text, never raw HTML. There are no headings
         to index in that mode, so the section nav stays hidden. */
      el.reportBody.classList.add("is-plain");
      el.reportBody.textContent = markdown;
      buildReportNav();
      return;
    }
    el.reportBody.classList.remove("is-plain");
    const rawHtml = window.marked.parse(markdown);
    const clean = window.DOMPurify.sanitize(rawHtml);
    el.reportBody.innerHTML = clean;
    wrapWideBlocks();
    buildReportNav();
  }

  /* The model can emit a table of any width. Give each one its own scroll
     container so a wide table never drags the whole page sideways. */
  function wrapWideBlocks() {
    el.reportBody.querySelectorAll("table").forEach((table) => {
      if (table.parentElement && table.parentElement.classList.contains("prose__scroll")) return;
      const wrap = document.createElement("div");
      wrap.className = "prose__scroll";
      wrap.setAttribute("tabindex", "0");
      wrap.setAttribute("role", "region");
      wrap.setAttribute("aria-label", "Table, scrollable");
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }

  /* Index whatever headings the model actually produced, rather than
     assuming the four prompted sections exist. */
  function buildReportNav() {
    if (tocObserver) { tocObserver.disconnect(); tocObserver = null; }
    el.reportToc.innerHTML = "";

    const headings = Array.prototype.slice.call(
      el.reportBody.querySelectorAll("h1, h2, h3")
    ).filter((h) => h.textContent.trim());

    if (headings.length < 2) {
      el.reportNav.classList.add("is-hidden");
      return;
    }
    el.reportNav.classList.remove("is-hidden");

    const links = [];
    headings.forEach((h, i) => {
      const id = "section-" + i;
      h.id = id;

      const li = document.createElement("li");
      const a = document.createElement("button");
      a.type = "button";
      a.className = "tocnav__link" + (h.tagName === "H3" ? " tocnav__link--sub" : "");
      a.textContent = h.textContent.trim().replace(/^\d+\.\s*/, "");
      a.addEventListener("click", () => {
        h.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
      });
      li.appendChild(a);
      el.reportToc.appendChild(li);
      links.push({ a, h });
    });

    if (!("IntersectionObserver" in window)) {
      links[0].a.classList.add("is-current");
      return;
    }

    const visible = new Set();
    tocObserver = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) visible.add(e.target); else visible.delete(e.target);
      });
      let current = null;
      for (let i = 0; i < links.length; i++) {
        if (visible.has(links[i].h)) { current = links[i]; break; }
      }
      if (!current) return;
      links.forEach((l) => l.a.classList.toggle("is-current", l === current));
    }, { rootMargin: "-84px 0px -60% 0px", threshold: 0 });

    headings.forEach((h) => tocObserver.observe(h));
    links[0].a.classList.add("is-current");
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function applyTab(tab) {
    state.activeTab = tab;
    const reportActive = tab === "report";
    el.tabReport.setAttribute("aria-selected", String(reportActive));
    el.tabRaw.setAttribute("aria-selected", String(!reportActive));
    el.panelReport.classList.toggle("is-hidden", !reportActive);
    el.panelRaw.classList.toggle("is-hidden", reportActive);
  }

  function renderRawTab() {
    if (!state.lastResult) return;
    const data = state.lastResult;
    const filter = state.filterText.trim().toLowerCase();

    el.modeGrouped.setAttribute("aria-pressed", String(state.rawMode === "grouped"));
    el.modeFlat.setAttribute("aria-pressed", String(state.rawMode === "flat"));
    el.groupView.classList.toggle("is-hidden", state.rawMode !== "grouped");
    el.flatView.classList.toggle("is-hidden", state.rawMode !== "flat");

    const matchedKeywords = filter
      ? data.keywords.filter((k) => k.keyword.toLowerCase().indexOf(filter) !== -1)
      : data.keywords;

    announce(filter
      ? `${matchedKeywords.length} of ${data.keywords.length} keywords match “${state.filterText.trim()}”`
      : `${data.keywords.length} keywords across ${data.sources.length} source queries`);

    if (state.rawMode === "grouped") {
      renderGroupedView(data, filter);
    } else {
      renderFlatView(matchedKeywords, filter);
    }
  }

  function emptyResult(container, title, text) {
    const wrap = document.createElement("div");
    wrap.className = "void";
    wrap.appendChild(icon("search", "void__icon"));
    const h = document.createElement("p");
    h.className = "void__title";
    h.textContent = title;
    const p = document.createElement("p");
    p.className = "void__text";
    p.textContent = text;
    wrap.appendChild(h);
    wrap.appendChild(p);
    container.appendChild(wrap);
  }

  function renderGroupedView(data, filter) {
    el.groupView.innerHTML = "";
    const frag = document.createDocumentFragment();
    let shown = 0;

    data.sources.forEach((source, idx) => {
      const kws = filter
        ? source.keywords.filter((k) => k.toLowerCase().indexOf(filter) !== -1)
        : source.keywords;
      if (filter && kws.length === 0) return;
      shown++;

      const details = document.createElement("details");
      details.className = "group";
      if (filter || idx === 0) details.open = true;

      const summary = document.createElement("summary");
      summary.className = "group__summary";
      summary.appendChild(icon("chevron-right", "group__chev"));

      const queryEl = document.createElement("span");
      queryEl.className = "group__query";
      queryEl.textContent = source.query;

      const typeEl = document.createElement("span");
      typeEl.className = `tag tag--${source.type}`;
      typeEl.textContent = source.type;

      const countEl = document.createElement("span");
      countEl.className = "group__count";
      countEl.textContent = String(kws.length);

      summary.appendChild(queryEl);
      summary.appendChild(typeEl);
      summary.appendChild(countEl);
      details.appendChild(summary);

      const body = document.createElement("div");
      body.className = "group__body";
      kws.forEach((k) => {
        const kEl = document.createElement("div");
        kEl.className = "group__kw";
        kEl.textContent = k;
        body.appendChild(kEl);
      });
      details.appendChild(body);
      frag.appendChild(details);
    });

    el.groupView.appendChild(frag);
    if (!shown) {
      emptyResult(el.groupView, "No matches",
        `No source query contains a keyword matching “${state.filterText.trim()}”.`);
    }
  }

  function renderFlatView(keywords, filter) {
    el.flatView.innerHTML = "";

    if (!keywords.length) {
      emptyResult(el.flatView, "No matches",
        `No keyword contains “${state.filterText.trim()}”.`);
      return;
    }

    const frag = document.createDocumentFragment();
    keywords.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "flat__row";

      const main = document.createElement("div");
      const kEl = document.createElement("div");
      kEl.className = "flat__kw";
      kEl.textContent = entry.keyword;
      const sourcesEl = document.createElement("div");
      sourcesEl.className = "flat__src";
      sourcesEl.textContent = entry.sources.join("  ·  ");
      sourcesEl.title = entry.sources.join(", ");
      main.appendChild(kEl);
      main.appendChild(sourcesEl);

      const tags = document.createElement("div");
      tags.className = "flat__tags";
      entry.types.forEach((t) => {
        const tag = document.createElement("span");
        tag.className = `tag tag--${t}`;
        tag.textContent = t;
        tags.appendChild(tag);
      });

      row.appendChild(main);
      row.appendChild(tags);
      frag.appendChild(row);
    });
    el.flatView.appendChild(frag);
  }

  /* ------------------------------------------------------------------
     RUN ORCHESTRATION
     ------------------------------------------------------------------ */

  async function runAnalysis(topic) {
    clearError();
    closeMenu();
    setFormDisabled(true);
    showRunning();

    state.cancelledByUser = false;
    state.timedOut = false;
    state.formHeldDisabled = false;
    state.elapsedSeconds = 0;
    updateRunningMessage();

    state.runTimerId = setInterval(() => {
      state.elapsedSeconds++;
      updateRunningMessage();
    }, 1000);

    const controller = new AbortController();
    state.abortController = controller;
    state.timeoutId = setTimeout(() => {
      state.timedOut = true;
      controller.abort();
    }, CLIENT_TIMEOUT_MS);

    try {
      const data = state.mockMode
        ? await mockAnalyze(topic, controller.signal)
        : await apiAnalyze(topic, controller.signal);
      renderResults(data);
      if (state.mockMode) renderQuotaLine(); else await refreshStatusQuietly();
    } catch (err) {
      handleAnalyzeError(err, topic);
      if (state.mockMode) renderQuotaLine(); else await refreshStatusQuietly();
    } finally {
      clearTimeout(state.timeoutId);
      clearInterval(state.runTimerId);
      hideRunning();
      if (!state.formHeldDisabled) setFormDisabled(false);
      state.abortController = null;
    }
  }

  async function refreshStatusQuietly() {
    try {
      state.status = await apiStatus();
      renderQuotaLine();
      renderRunParameters();
    } catch (e) {
      /* quota display is best-effort; a failed refresh should not surface an error */
    }
  }

  function handleAnalyzeError(err, topic) {
    if (err && err.name === "AbortError") {
      if (state.cancelledByUser) {
        showNote("Run cancelled.");
        return;
      }
      showError({
        title: "The run timed out",
        message: `No response after ${CLIENT_TIMEOUT_MS / 1000} seconds. The backend may be under load.`,
        actionLabel: "Try again",
        onAction: () => runAnalysis(topic)
      });
      return;
    }

    if (err instanceof ApiError) {
      handleApiError(err, topic);
      return;
    }

    logConnectionDiagnostics(err);
    showError({
      title: "Connection problem",
      message:
        "Could not reach the server. Check that the backend is running, " +
        "then see the browser console for details.",
      actionLabel: "Try again",
      onAction: () => runAnalysis(topic)
    });
  }

  function handleApiError(err, topic) {
    const detail = err.body && err.body.detail;

    switch (err.status) {
      case 400:
        showError({
          title: "That topic will not work",
          message: detail || "Topic must not be empty.",
          actionLabel: "Edit topic",
          onAction: () => el.topicInput.focus()
        });
        break;

      case 401:
        clearSession();
        showGate("Your session has expired. Sign in again.");
        break;

      case 429: {
        const retryAfter = (err.body && err.body.retry_after_seconds) || 0;
        state.formHeldDisabled = true;
        setFormDisabled(true);
        showError({
          title: "Search limit reached",
          message: "The hourly limit is shared across the whole workspace. It refills on a rolling window.",
          countdownSeconds: retryAfter,
          onCountdownEnd: () => {
            state.formHeldDisabled = false;
            setFormDisabled(false);
            refreshStatusQuietly();
          }
        });
        break;
      }

      case 502:
      case 504:
        showError({
          title: err.status === 504 ? "The run timed out" : "The run failed",
          message: detail || "Something went wrong on the server.",
          actionLabel: "Try again",
          onAction: () => runAnalysis(topic)
        });
        break;

      default:
        showError({
          title: "Something went wrong",
          message: detail || `The server returned an unexpected error (${err.status}).`,
          actionLabel: "Try again",
          onAction: () => runAnalysis(topic)
        });
    }
  }

  /* ------------------------------------------------------------------
     DOWNLOADS
     ------------------------------------------------------------------ */

  function buildCsv(data) {
    const rows = [["keyword", "sources", "types"]];
    data.keywords.forEach((k) => rows.push([k.keyword, k.sources.join(", "), k.types.join(", ")]));
    return "﻿" + rows.map((r) => r.map(csvField).join(",")).join("\r\n");
  }

  function filenameFor(prefix, topic, generatedAt, ext) {
    return `${prefix}-${slugify(topic)}-${todayIso(generatedAt)}.${ext}`;
  }

  /* ------------------------------------------------------------------
     EVENTS
     ------------------------------------------------------------------ */

  el.gateForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearGateError();
    const username = el.gateUsername.value.trim();
    const password = el.gatePassword.value;
    if (!username || !password) {
      showGateError("Enter both a username and a password.");
      (username ? el.gatePassword : el.gateUsername).focus();
      return;
    }

    el.gateSubmit.disabled = true;
    try {
      const result = await apiLogin(username, password);
      /* Only the returned session token is stored — never the password. */
      state.accessKey = (result && result.access_key) || null;
      if (!state.accessKey) throw new Error("No access key returned");
      try { sessionStorage.setItem(ACCESS_KEY_STORAGE, state.accessKey); } catch (e) { /* private mode */ }
      state.status = await apiStatus();
      setIdentity(username);
      showApp();
      renderQuotaLine();
      renderRunParameters();
      setTimeout(() => el.topicInput.focus(), 0);
    } catch (err) {
      clearSession();
      if (err instanceof ApiError) {
        showGateError((err.body && err.body.detail) || "Invalid username or password.");
      } else {
        logConnectionDiagnostics(err);
        showGateError(
          "Could not reach the server. Check that the backend is running, " +
            "then see the browser console for details."
        );
      }
      el.gatePassword.value = "";
      el.gatePassword.focus();
    } finally {
      el.gateSubmit.disabled = false;
    }
  });

  el.gateUsername.addEventListener("input", clearGateError);
  el.gatePassword.addEventListener("input", clearGateError);

  el.accountBtn.addEventListener("click", () => toggleMenu(el.accountBtn, el.accountMenu));
  el.exportBtn.addEventListener("click", () => toggleMenu(el.exportBtn, el.exportMenu));

  el.logoutBtn.addEventListener("click", async () => {
    const key = state.accessKey;
    closeMenu();
    clearSession();
    showGate();
    if (key) {
      /* Best effort: the local session is already gone either way. */
      try { await apiLogout(key); } catch (e) { /* ignore */ }
    }
  });

  exampleChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      el.topicInput.value = chip.getAttribute("data-topic") || "";
      el.topicInput.focus();
    });
  });

  el.topicForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const topic = el.topicInput.value.trim();
    el.topicError.classList.add("is-hidden");
    el.topicInput.removeAttribute("aria-invalid");

    if (!topic) {
      showTopicError("Enter a topic to analyse.");
      return;
    }
    if (topic.length > 100) {
      showTopicError("Topic must be 100 characters or fewer.");
      return;
    }
    runAnalysis(topic);
  });

  function showTopicError(message) {
    el.topicErrorText.textContent = message;
    el.topicError.classList.remove("is-hidden");
    el.topicInput.setAttribute("aria-invalid", "true");
    el.topicInput.focus();
  }

  el.cancelBtn.addEventListener("click", () => {
    state.cancelledByUser = true;
    if (state.abortController) state.abortController.abort();
  });

  el.tabReport.addEventListener("click", () => applyTab("report"));
  el.tabRaw.addEventListener("click", () => applyTab("raw"));

  document.querySelector('[role="tablist"]').addEventListener("keydown", (e) => {
    const tabs = [el.tabReport, el.tabRaw];
    const idx = tabs.indexOf(document.activeElement);
    if (idx === -1) return;
    let next = null;
    if (e.key === "ArrowRight") next = tabs[(idx + 1) % tabs.length];
    if (e.key === "ArrowLeft") next = tabs[(idx - 1 + tabs.length) % tabs.length];
    if (e.key === "Home") next = tabs[0];
    if (e.key === "End") next = tabs[tabs.length - 1];
    if (next) { e.preventDefault(); next.focus(); next.click(); }
  });

  el.modeGrouped.addEventListener("click", () => { state.rawMode = "grouped"; renderRawTab(); });
  el.modeFlat.addEventListener("click", () => { state.rawMode = "flat"; renderRawTab(); });

  const onFilterInput = debounce(() => {
    state.filterText = el.rawFilter.value;
    renderRawTab();
  }, 120);
  el.rawFilter.addEventListener("input", onFilterInput);

  el.copyAllBtn.addEventListener("click", async () => {
    if (!state.lastResult) return;
    const filter = state.filterText.trim().toLowerCase();
    const list = filter
      ? state.lastResult.keywords.filter((k) => k.keyword.toLowerCase().indexOf(filter) !== -1)
      : state.lastResult.keywords;
    const ok = await copyToClipboard(list.map((k) => k.keyword).join("\n"));
    flashButton(el.copyAllBtn, ok, "Copied", "Failed");
  });

  el.copyReportBtn.addEventListener("click", async () => {
    if (!state.lastResult) return;
    const ok = await copyToClipboard(state.lastResult.analysis_markdown);
    flashButton(el.copyReportBtn, ok, "Copied", "Failed");
  });

  el.downloadCsvBtn.addEventListener("click", () => {
    if (!state.lastResult) return;
    closeMenu();
    const data = state.lastResult;
    downloadTextFile(
      filenameFor("keywords", data.topic, data.generated_at, "csv"),
      "text/csv;charset=utf-8",
      buildCsv(data)
    );
  });

  el.downloadReportBtn.addEventListener("click", () => {
    if (!state.lastResult) return;
    closeMenu();
    const data = state.lastResult;
    downloadTextFile(
      filenameFor("report", data.topic, data.generated_at, "md"),
      "text/markdown;charset=utf-8",
      data.analysis_markdown
    );
  });

  /* ------------------------------------------------------------------
     INIT
     ------------------------------------------------------------------ */

  async function init() {
    if (window.__libLoadFailed || !librariesAvailable()) {
      el.libError.classList.remove("is-hidden");
    }

    const params = new URLSearchParams(location.search);
    state.mockMode = params.get("mock") === "1";

    if (state.mockMode) {
      setIdentity("mock");
      showApp();
      renderQuotaLine();
      renderRunParameters();
      return;
    }

    let storedKey = null;
    try { storedKey = sessionStorage.getItem(ACCESS_KEY_STORAGE); } catch (e) { /* private mode */ }
    if (!storedKey) { showGate(); return; }

    state.accessKey = storedKey;
    try {
      state.status = await apiStatus();
      setIdentity("admin");
      showApp();
      renderQuotaLine();
      renderRunParameters();
    } catch (e) {
      clearSession();
      showGate(e instanceof ApiError && e.status === 401
        ? "Your session has expired. Sign in again."
        : undefined);
    }
  }

  init();
})();

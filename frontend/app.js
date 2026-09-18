/* ==========================================================================
   Keyword Suggest & Intent Analyzer — app logic

   The backend is a separate service on its own origin. Its base URL comes
   from window.APP_CONFIG.API_BASE_URL in config.js, which build.sh rewrites
   at deploy time; an empty value means "same origin as this page".

   Add ?mock=1 to the URL to bypass the backend entirely and exercise the
   whole UI against mock-response.json — mock and real runs both end in the
   same rendering path for results.
   ========================================================================== */

(function () {
  "use strict";

  /* ------------------------------------------------------------------
     STATE
     ------------------------------------------------------------------ */

  /* Holds the opaque session token returned by POST /api/login. The
     password itself is never stored. */
  const ACCESS_KEY_STORAGE = "kria_access_key";

  /* Backend origin, without a trailing slash. Empty means same-origin. */
  const API_BASE_URL = String(
    (window.APP_CONFIG && window.APP_CONFIG.API_BASE_URL) || ""
  ).replace(/\/+$/, "");
  const CLIENT_TIMEOUT_MS = 180000;
  const RUN_STAGE_SWITCH_SECONDS = 35;
  const MOCK_DELAY_MS = 6000;

  const state = {
    accessKey: null,
    mockMode: false,
    mockSearchesRemaining: 6,
    status: null,
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
    elapsedSeconds: 0
  };

  /* ------------------------------------------------------------------
     DOM references
     ------------------------------------------------------------------ */

  const $ = (id) => document.getElementById(id);

  const el = {
    libError: $("lib-load-error"),
    gateScreen: $("gate-screen"),
    gateForm: $("gate-form"),
    gateUsername: $("gate-username"),
    gatePassword: $("gate-password"),
    gateError: $("gate-error"),
    gateSubmit: $("gate-submit"),
    app: $("app"),
    quotaLine: $("quota-line"),
    logoutBtn: $("logout-btn"),
    topicForm: $("topic-form"),
    topicInput: $("topic-input"),
    topicError: $("topic-error"),
    runBtn: $("run-btn"),
    exampleBtn: $("example-btn"),
    statusRegion: $("status-region"),
    runningStatus: $("running-status"),
    runningMessage: $("running-message"),
    elapsedValue: $("elapsed-value"),
    cancelBtn: $("cancel-btn"),
    runNote: $("run-note"),
    errorBanner: $("error-banner"),
    errorTitle: $("error-title"),
    errorMessage: $("error-message"),
    errorCountdown: $("error-countdown"),
    errorActionBtn: $("error-action-btn"),
    results: $("results"),
    cachedBadge: $("cached-badge"),
    resultsTopic: $("results-topic"),
    downloadReportBtn: $("download-report-btn"),
    downloadCsvBtn: $("download-csv-btn"),
    statTotal: $("stat-total"),
    statQueries: $("stat-queries"),
    statDuration: $("stat-duration"),
    partialBanner: $("partial-banner"),
    partialTitle: $("partial-title"),
    failedQueriesList: $("failed-queries-list"),
    tabReport: $("tab-report"),
    tabRaw: $("tab-raw"),
    panelReport: $("panel-report"),
    panelRaw: $("panel-raw"),
    copyReportBtn: $("copy-report-btn"),
    reportBody: $("report-body"),
    rawFilter: $("raw-filter"),
    modeGrouped: $("mode-grouped"),
    modeFlat: $("mode-flat"),
    copyAllBtn: $("copy-all-btn"),
    filterSummary: $("filter-summary"),
    groupView: $("group-view"),
    flatView: $("flat-view")
  };

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
     RENDER
     ------------------------------------------------------------------ */

  function showGate(message) {
    el.app.classList.add("hidden");
    el.gateScreen.classList.remove("hidden");
    el.gatePassword.value = "";
    if (message) showGateError(message);
    setTimeout(() => el.gateUsername.focus(), 0);
  }

  /* The stored access key is a server-issued session token, never the
     password. Any 401 means it is gone or expired, so it is cleared here and
     the user is sent back to the gate. */
  function clearSession() {
    sessionStorage.removeItem(ACCESS_KEY_STORAGE);
    state.accessKey = null;
    state.status = null;
  }

  function showApp() {
    el.gateScreen.classList.add("hidden");
    el.app.classList.remove("hidden");
  }

  function showGateError(message) {
    el.gateError.textContent = message;
    el.gateError.classList.remove("hidden");
    el.gateUsername.setAttribute("aria-invalid", "true");
    el.gatePassword.setAttribute("aria-invalid", "true");
  }

  function clearGateError() {
    el.gateError.classList.add("hidden");
    el.gateUsername.removeAttribute("aria-invalid");
    el.gatePassword.removeAttribute("aria-invalid");
  }

  function renderQuotaLine() {
    if (state.mockMode) {
      el.quotaLine.textContent = `${state.mockSearchesRemaining} searches left this hour (mock)`;
      return;
    }
    if (!state.status) { el.quotaLine.textContent = ""; return; }
    el.quotaLine.textContent = `${state.status.searches_remaining} searches left this hour`;
  }

  function setFormDisabled(disabled) {
    el.topicInput.disabled = disabled;
    el.runBtn.disabled = disabled;
    el.exampleBtn.disabled = disabled;
  }

  function showRunning() {
    el.statusRegion.classList.remove("hidden");
    el.runningStatus.classList.remove("hidden");
    el.errorBanner.classList.add("hidden");
  }

  function hideRunning() {
    el.runningStatus.classList.add("hidden");
    if (el.errorBanner.classList.contains("hidden") && el.runNote.classList.contains("hidden")) {
      el.statusRegion.classList.add("hidden");
    }
  }

  function updateRunningMessage() {
    el.runningMessage.textContent = state.elapsedSeconds < RUN_STAGE_SWITCH_SECONDS
      ? "Collecting Google suggestions… this takes about 40 seconds"
      : "Running AI analysis…";
    el.elapsedValue.textContent = String(state.elapsedSeconds);
  }

  let countdownIntervalId = null;

  function clearErrorCountdown() {
    if (countdownIntervalId) { clearInterval(countdownIntervalId); countdownIntervalId = null; }
  }

  function showError(options) {
    clearErrorCountdown();
    el.statusRegion.classList.remove("hidden");
    el.errorBanner.classList.remove("hidden");
    el.errorTitle.textContent = options.title;
    el.errorMessage.textContent = options.message;

    if (options.actionLabel) {
      el.errorActionBtn.textContent = options.actionLabel;
      el.errorActionBtn.classList.remove("hidden");
      el.errorActionBtn.onclick = options.onAction || null;
    } else {
      el.errorActionBtn.classList.add("hidden");
      el.errorActionBtn.onclick = null;
    }

    if (typeof options.countdownSeconds === "number") {
      el.errorCountdown.classList.remove("hidden");
      let remaining = options.countdownSeconds;
      const tick = () => { el.errorCountdown.textContent = formatCountdown(remaining); };
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
      el.errorCountdown.classList.add("hidden");
    }
  }

  function showNote(message) {
    el.statusRegion.classList.remove("hidden");
    el.runNote.textContent = message;
    el.runNote.classList.remove("hidden");
    setTimeout(() => {
      el.runNote.classList.add("hidden");
      if (el.runningStatus.classList.contains("hidden") && el.errorBanner.classList.contains("hidden")) {
        el.statusRegion.classList.add("hidden");
      }
    }, 3000);
  }

  function clearError() {
    clearErrorCountdown();
    el.errorBanner.classList.add("hidden");
    if (el.runningStatus.classList.contains("hidden") && el.runNote.classList.contains("hidden")) {
      el.statusRegion.classList.add("hidden");
    }
  }

  function formatCountdown(totalSeconds) {
    const s = Math.max(0, totalSeconds);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
  }

  function renderResults(data) {
    state.lastResult = data;

    el.resultsTopic.textContent = data.topic;
    el.cachedBadge.classList.toggle("hidden", !data.cached);
    el.statTotal.textContent = String(data.total_keywords);
    el.statQueries.textContent = `${data.queries_succeeded} / ${data.queries_attempted}`;
    el.statDuration.textContent = `${Math.round(data.duration_seconds)}s`;

    const hasFailed = Array.isArray(data.failed_queries) && data.failed_queries.length > 0;
    el.partialBanner.classList.toggle("hidden", !hasFailed);
    if (hasFailed) {
      el.partialTitle.textContent = `Partial results — ${data.queries_succeeded} of ${data.queries_attempted} queries answered`;
      el.failedQueriesList.innerHTML = "";
      const frag = document.createDocumentFragment();
      data.failed_queries.forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q;
        frag.appendChild(li);
      });
      el.failedQueriesList.appendChild(frag);
    }

    renderReportTab(data.analysis_markdown);
    state.filterText = "";
    el.rawFilter.value = "";
    renderRawTab();

    el.results.classList.remove("hidden");
  }

  function renderReportTab(markdown) {
    if (!librariesAvailable()) {
      el.reportBody.textContent = markdown;
      return;
    }
    const rawHtml = window.marked.parse(markdown);
    const clean = window.DOMPurify.sanitize(rawHtml);
    el.reportBody.innerHTML = clean;
  }

  function applyTab(tab) {
    state.activeTab = tab;
    const reportActive = tab === "report";
    el.tabReport.setAttribute("aria-selected", String(reportActive));
    el.tabRaw.setAttribute("aria-selected", String(!reportActive));
    el.panelReport.classList.toggle("hidden", !reportActive);
    el.panelRaw.classList.toggle("hidden", reportActive);
  }

  function renderRawTab() {
    if (!state.lastResult) return;
    const data = state.lastResult;
    const filter = state.filterText.trim().toLowerCase();

    el.modeGrouped.setAttribute("aria-pressed", String(state.rawMode === "grouped"));
    el.modeFlat.setAttribute("aria-pressed", String(state.rawMode === "flat"));
    el.groupView.classList.toggle("hidden", state.rawMode !== "grouped");
    el.flatView.classList.toggle("hidden", state.rawMode !== "flat");

    const matchedKeywords = filter
      ? data.keywords.filter((k) => k.keyword.toLowerCase().indexOf(filter) !== -1)
      : data.keywords;

    announce(filter
      ? `${matchedKeywords.length} of ${data.keywords.length} keywords match “${state.filterText.trim()}”`
      : `${data.keywords.length} keywords across ${data.sources.length} source queries`);

    if (state.rawMode === "grouped") {
      renderGroupedView(data, filter);
    } else {
      renderFlatView(matchedKeywords);
    }
  }

  function renderGroupedView(data, filter) {
    el.groupView.innerHTML = "";
    const frag = document.createDocumentFragment();

    data.sources.forEach((source, idx) => {
      const kws = filter ? source.keywords.filter((k) => k.toLowerCase().indexOf(filter) !== -1) : source.keywords;
      if (filter && kws.length === 0) return;

      const details = document.createElement("details");
      details.className = "group-row";
      if (filter || idx === 0) details.open = true;

      const summary = document.createElement("summary");
      const queryEl = document.createElement("span");
      queryEl.className = "group-query";
      queryEl.textContent = source.query;
      const typeEl = document.createElement("span");
      typeEl.className = "group-type";
      typeEl.textContent = source.type;
      const countEl = document.createElement("span");
      countEl.className = "group-count";
      countEl.textContent = String(kws.length);
      summary.appendChild(queryEl);
      summary.appendChild(typeEl);
      summary.appendChild(countEl);
      details.appendChild(summary);

      const body = document.createElement("div");
      body.className = "group-keywords";
      kws.forEach((k) => {
        const kEl = document.createElement("div");
        kEl.className = "group-keyword";
        kEl.textContent = k;
        body.appendChild(kEl);
      });
      details.appendChild(body);

      frag.appendChild(details);
    });

    el.groupView.appendChild(frag);
  }

  function renderFlatView(keywords) {
    el.flatView.innerHTML = "";
    const frag = document.createDocumentFragment();

    keywords.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "flat-row";
      const kEl = document.createElement("div");
      kEl.className = "flat-keyword";
      kEl.textContent = entry.keyword;
      const sourcesEl = document.createElement("div");
      sourcesEl.className = "flat-sources";
      sourcesEl.textContent = `from: ${entry.sources.join(", ")}`;
      row.appendChild(kEl);
      row.appendChild(sourcesEl);
      frag.appendChild(row);
    });

    el.flatView.appendChild(frag);
  }

  /* ------------------------------------------------------------------
     Run orchestration
     ------------------------------------------------------------------ */

  async function runAnalysis(topic) {
    clearError();
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

    showError({
      title: "Connection problem",
      message: "Could not reach the server. Check your connection and try again.",
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
          message: "The hourly limit is shared across the whole team. Wait for it to reset, or ask a teammate if this is urgent.",
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
     Downloads
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
      sessionStorage.setItem(ACCESS_KEY_STORAGE, state.accessKey);
      state.status = await apiStatus();
      showApp();
      renderQuotaLine();
    } catch (err) {
      clearSession();
      if (err instanceof ApiError) {
        showGateError((err.body && err.body.detail) || "Invalid username or password.");
      } else {
        showGateError("Could not reach the server. Check your connection and try again.");
      }
      el.gatePassword.value = "";
      el.gatePassword.focus();
    } finally {
      el.gateSubmit.disabled = false;
    }
  });

  el.gateUsername.addEventListener("input", clearGateError);
  el.gatePassword.addEventListener("input", clearGateError);

  el.logoutBtn.addEventListener("click", async () => {
    const key = state.accessKey;
    clearSession();
    showGate();
    if (key) {
      /* Best effort: the local session is already gone either way. */
      try {
        await fetch(apiUrl("/api/logout"), { method: "POST", headers: { "X-Access-Key": key } });
      } catch (e) { /* ignore */ }
    }
  });

  el.exampleBtn.addEventListener("click", () => {
    el.topicInput.value = "luxury villa rentals";
    el.topicInput.focus();
  });

  el.topicForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const topic = el.topicInput.value.trim();
    el.topicError.classList.add("hidden");
    el.topicInput.removeAttribute("aria-invalid");

    if (!topic) {
      el.topicError.textContent = "Enter a topic to analyse.";
      el.topicError.classList.remove("hidden");
      el.topicInput.setAttribute("aria-invalid", "true");
      el.topicInput.focus();
      return;
    }
    if (topic.length > 100) {
      el.topicError.textContent = "Topic must be 100 characters or fewer.";
      el.topicError.classList.remove("hidden");
      el.topicInput.setAttribute("aria-invalid", "true");
      el.topicInput.focus();
      return;
    }
    runAnalysis(topic);
  });

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
    const original = el.copyAllBtn.textContent;
    el.copyAllBtn.textContent = ok ? "Copied" : "Could not copy";
    setTimeout(() => { el.copyAllBtn.textContent = original; }, 1600);
  });

  el.copyReportBtn.addEventListener("click", async () => {
    if (!state.lastResult) return;
    const ok = await copyToClipboard(state.lastResult.analysis_markdown);
    const original = el.copyReportBtn.textContent;
    el.copyReportBtn.textContent = ok ? "Copied" : "Could not copy";
    setTimeout(() => { el.copyReportBtn.textContent = original; }, 1600);
  });

  el.downloadCsvBtn.addEventListener("click", () => {
    if (!state.lastResult) return;
    const data = state.lastResult;
    downloadTextFile(filenameFor("keywords", data.topic, data.generated_at, "csv"), "text/csv;charset=utf-8", buildCsv(data));
  });

  el.downloadReportBtn.addEventListener("click", () => {
    if (!state.lastResult) return;
    const data = state.lastResult;
    downloadTextFile(filenameFor("report", data.topic, data.generated_at, "md"), "text/markdown;charset=utf-8", data.analysis_markdown);
  });

  /* ------------------------------------------------------------------
     Init
     ------------------------------------------------------------------ */

  async function init() {
    if (window.__libLoadFailed || !librariesAvailable()) {
      el.libError.classList.remove("hidden");
    }

    const params = new URLSearchParams(location.search);
    state.mockMode = params.get("mock") === "1";

    if (state.mockMode) {
      showApp();
      renderQuotaLine();
      return;
    }

    const storedKey = sessionStorage.getItem(ACCESS_KEY_STORAGE);
    if (!storedKey) { showGate(); return; }

    state.accessKey = storedKey;
    try {
      state.status = await apiStatus();
      showApp();
      renderQuotaLine();
    } catch (e) {
      clearSession();
      showGate(e instanceof ApiError && e.status === 401
        ? "Your session has expired. Sign in again."
        : undefined);
    }
  }

  init();
})();

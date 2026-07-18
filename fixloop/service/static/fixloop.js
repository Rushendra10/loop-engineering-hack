(() => {
  "use strict";

  const STAGES = ["queued", "clone", "agent", "verify", "pr", "finished"];
  const STAGE_ALIASES = {
    pending: "queued", accepted: "queued",
    cloning: "clone", checkout: "clone", issue: "clone", profiling: "clone",
    cursor: "agent", coding: "agent", worker: "agent", agent_retry: "agent",
    testing: "verify", verifier: "verify", verification: "verify", buildkite: "verify", verify_retry: "verify",
    pull_request: "pr", opening_pr: "pr", pushing: "pr", close_issue: "pr",
    done: "finished", complete: "finished", completed: "finished",
  };
  const FALLBACK_EVENTS = {
    queued: "Control plane accepted the request",
    clone: "Provisioning workspace and cloning repository",
    agent: "Cursor agent is writing a regression test and root-cause fix",
    verify: "Independent verifier is challenging the two-commit branch",
    pr: "Publishing verified branch and resolving the source issue",
    finished: "Execution sealed; artifacts are ready",
  };
  const SOURCE_BY_STAGE = {
    queued: "orchestrator", clone: "akash", agent: "cursor",
    verify: "buildkite", pr: "github", finished: "orchestrator",
  };
  const EVENT_SOURCES = new Set(["akash", "x402", "cursor", "buildkite", "github", "verifier", "orchestrator"]);

  const $ = (id) => document.getElementById(id);
  const fixForm = $("fix-form");
  const jobForm = $("job-form");
  const runConsole = $("run-console");
  const requestNotice = $("request-notice");
  const runError = $("run-error");
  const verdictPanel = $("verdict-panel");
  const startButton = $("start-button");
  let pollTimer = null;
  let currentJobId = null;
  let renderedEventCount = 0;
  let fallbackStages = new Set();
  let systemMetadata = {};

  function normalizeStage(stage) {
    if (typeof stage !== "string") return null;
    const key = stage.trim().toLowerCase().replace(/[\s-]+/g, "_");
    return STAGES.includes(key) ? key : STAGE_ALIASES[key] || null;
  }

  function isFinished(job) {
    const status = String(job.status || "").toLowerCase();
    return ["done", "finished", "complete", "completed", "failed", "error"].includes(status)
      || normalizeStage(job.stage) === "finished";
  }

  function setNotice(kind, title, message) {
    requestNotice.replaceChildren();
    requestNotice.className = `request-notice ${kind || ""}`.trim();
    const heading = document.createElement("strong");
    heading.textContent = title;
    const copy = document.createElement("span");
    copy.textContent = message;
    requestNotice.append(heading, copy);
    requestNotice.hidden = false;
  }

  function clearNotice() {
    requestNotice.hidden = true;
    requestNotice.replaceChildren();
  }

  async function responseMessage(response) {
    try {
      const data = await response.json();
      if (typeof data === "string") return data;
      if (data && typeof data === "object") {
        const detail = data.detail || data.error || data.message;
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join(", ");
      }
    } catch (_) {
      // Empty x402 and proxy responses are handled by the status text below.
    }
    return response.statusText || `HTTP ${response.status}`;
  }

  function requestedSettings() {
    return {
      model: $("model-input").value,
      deadline_s: Number.parseInt($("deadline-input").value, 10),
      retry_on_rejection: $("retry-input").checked,
      close_issue: $("close-issue-input").checked,
    };
  }

  async function createFix(event) {
    event.preventDefault();
    clearNotice();
    if (!fixForm.reportValidity()) return;

    const payload = {
      repo: $("repo-input").value.trim(),
      issue: Number.parseInt($("issue-input").value, 10),
      ...requestedSettings(),
    };
    startButton.disabled = true;
    startButton.querySelector("span").textContent = "Reserving worker…";

    try {
      const response = await fetch("/fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (response.status === 402) {
        setNotice("payment-required", "Payment required · HTTP 402", "Complete the x402 challenge, then attach the returned job ID below.");
        return;
      }
      if (!response.ok) throw new Error(await responseMessage(response));
      const result = await response.json();
      if (!result || typeof result.job_id !== "string" || !result.job_id.trim()) {
        throw new Error("The control plane did not return a job ID.");
      }
      $("job-id-input").value = result.job_id.trim();
      setNotice("accepted", "Worker slot reserved", `Telemetry attached to job ${result.job_id.trim()}.`);
      followJob(result.job_id.trim());
    } catch (error) {
      setNotice("", "Launch failed", error.message || "The control plane did not respond.");
    } finally {
      startButton.disabled = false;
      startButton.querySelector("span").textContent = "Launch repair";
    }
  }

  function submitJobId(event) {
    event.preventDefault();
    const jobId = $("job-id-input").value.trim();
    if (!jobId) return $("job-id-input").focus();
    followJob(jobId);
  }

  function followJob(jobId) {
    currentJobId = jobId;
    window.clearTimeout(pollTimer);
    renderedEventCount = 0;
    fallbackStages = new Set();
    $("event-log").replaceChildren();
    $("log-count").textContent = "0";
    runConsole.hidden = false;
    verdictPanel.hidden = true;
    runError.hidden = true;
    $("job-id-display").textContent = jobId;
    $("run-heading").textContent = "Verification in progress";
    $("run-target").textContent = "Attaching to control-plane telemetry…";
    resetTelemetry();
    setLiveState(true);
    updateStages("queued", {}, false);
    appendTerminalEvent({ stage: "queued", source: "orchestrator", level: "system", message: "Telemetry channel attached" });
    runConsole.scrollIntoView({ behavior: "smooth", block: "start" });
    pollJob(jobId);
  }

  async function pollJob(jobId) {
    if (jobId !== currentJobId) return;
    try {
      const response = await fetch(`/job/${encodeURIComponent(jobId)}`, {
        headers: { Accept: "application/json" }, cache: "no-store",
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      const job = await response.json();
      if (!job || typeof job !== "object") throw new Error("Job telemetry was not valid JSON.");
      if (jobId !== currentJobId) return;
      renderJob(job);
      if (!isFinished(job)) pollTimer = window.setTimeout(() => pollJob(jobId), 1000);
      else setLiveState(false);
    } catch (error) {
      setLiveState(false);
      runError.textContent = `Could not load job ${jobId}: ${error.message || "unknown error"}`;
      runError.hidden = false;
    }
  }

  function setLiveState(live) {
    const indicator = $("live-indicator");
    indicator.classList.toggle("stopped", !live);
    indicator.lastChild.textContent = live ? " Polling live" : " Stream sealed";
  }

  function renderJob(job) {
    const final = isFinished(job);
    const failed = Boolean(job.error);
    updateStages(job.stage, job, final, failed);
    renderEvents(job);
    renderTelemetry(job);
    $("updated-time").textContent = `SYNC ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
    if (job.repo) {
      let slug = job.repo;
      try { slug = new URL(job.repo).pathname.replace(/^\//, "").replace(/\.git$/, ""); } catch (_) {}
      $("run-target").textContent = `${slug} · issue #${job.issue ?? "?"}`;
      $("run-heading").textContent = failed ? "Run stopped" : (final ? "Evidence sealed" : "Autonomous repair in progress");
    }
    runError.textContent = job.error ? String(job.error) : "";
    runError.hidden = !job.error;
    if (job.verdict && typeof job.verdict === "object") renderVerdict(job.verdict, job, final);
  }

  function updateStages(rawStage, job, final, failed = false) {
    const stage = normalizeStage(rawStage);
    const currentIndex = stage ? STAGES.indexOf(stage) : -1;
    const hasPr = Boolean(job && job.pr_url);
    const verdictName = job && job.verdict && job.verdict.verdict;
    const skipPr = final && !hasPr && verdictName !== "verified";
    document.querySelectorAll("#stage-rail li").forEach((item, index) => {
      const itemStage = item.dataset.stage;
      item.classList.remove("active", "complete", "failed", "skipped");
      if (failed) {
        if (index < Math.max(currentIndex, 1)) item.classList.add("complete");
        if (itemStage === "finished") item.classList.add("failed");
      } else if (itemStage === "pr" && skipPr) item.classList.add("skipped");
      else if (final || index < currentIndex) item.classList.add("complete");
      else if (index === currentIndex) item.classList.add("active");
    });
    const stageText = typeof rawStage === "string" && rawStage.trim() ? rawStage.trim() : "awaiting signal";
    $("stage-description").textContent = failed ? "Execution stopped · inspect terminal" : `PHASE / ${stageText.toUpperCase()}`;
  }

  function renderEvents(job) {
    const events = Array.isArray(job.events) ? job.events : [];
    if (events.length) {
      for (let index = renderedEventCount; index < events.length; index += 1) appendTerminalEvent(events[index]);
      renderedEventCount = events.length;
      return;
    }
    const normalized = normalizeStage(job.stage);
    if (normalized && !fallbackStages.has(normalized)) {
      fallbackStages.add(normalized);
      appendTerminalEvent({ stage: normalized, level: job.error ? "error" : "info", message: FALLBACK_EVENTS[normalized] });
    }
  }

  function appendTerminalEvent(event) {
    const log = $("event-log");
    log.querySelector(".terminal-empty")?.remove();
    const row = document.createElement("div");
    const level = ["success", "warn", "error", "system"].includes(event.level) ? event.level : "info";
    row.className = `log-line ${level}`;
    const time = document.createElement("time");
    const date = event.at ? new Date(event.at) : new Date();
    time.textContent = Number.isNaN(date.getTime()) ? "--:--:--" : date.toLocaleTimeString([], { hour12: false });
    const normalizedStage = normalizeStage(event.stage) || "queued";
    const requestedSource = String(event.source || SOURCE_BY_STAGE[normalizedStage] || "orchestrator").toLowerCase();
    const sourceName = EVENT_SOURCES.has(requestedSource) ? requestedSource : "orchestrator";
    const source = document.createElement("span");
    source.className = `log-source source-${sourceName}`;
    source.textContent = sourceName === "orchestrator" ? "CTRL" : sourceName.toUpperCase();
    const stage = document.createElement("span");
    stage.className = "log-stage";
    stage.textContent = String(event.stage || "system").toUpperCase();
    const message = document.createElement("span");
    message.className = "log-message";
    message.textContent = String(event.message || "event received");
    row.append(time, source, stage, message);
    log.append(row);
    while (log.children.length > 160) log.firstElementChild.remove();
    $("log-count").textContent = String(log.querySelectorAll(".log-line").length);
    log.scrollTop = log.scrollHeight;
  }

  function shortSha(value) {
    return typeof value === "string" && value ? value.slice(0, 7) : "pending";
  }

  function renderTelemetry(job) {
    const settings = job.settings && typeof job.settings === "object" ? job.settings : {};
    const profile = job.profile && typeof job.profile === "object" ? job.profile : {};
    const infrastructure = systemMetadata.infrastructure && typeof systemMetadata.infrastructure === "object" ? systemMetadata.infrastructure : {};
    const payment = infrastructure.x402 && typeof infrastructure.x402 === "object" ? infrastructure.x402 : {};
    const pipeline = infrastructure.buildkite && typeof infrastructure.buildkite === "object" ? infrastructure.buildkite : {};
    $("runtime-provider").textContent = systemMetadata.runtime || "Akash dCloud";
    $("runtime-payment").textContent = `x402 · ${payment.status || "configured"}`;
    $("runtime-model").textContent = settings.model || systemMetadata.default_model || "auto";
    $("runtime-verifier").textContent = settings.verifier || systemMetadata.verifier || "configured";
    $("runtime-pipeline").textContent = pipeline.pipeline || "fixloop-verifier";
    $("runtime-attempt").textContent = `${job.attempt || 1} / ${settings.retry_on_rejection === false ? 1 : 2}`;
    $("runtime-languages").textContent = Array.isArray(profile.languages) && profile.languages.length ? profile.languages.join(" + ") : "profiling…";
    const commits = job.commits && typeof job.commits === "object" ? job.commits : {};
    $("commit-base").textContent = shortSha(commits.base);
    $("commit-test").textContent = shortSha(commits.test);
    $("commit-fix").textContent = shortSha(commits.fix);

    const lifecycleState = $("issue-lifecycle-state");
    const lifecycleCopy = $("issue-lifecycle-copy");
    if (job.issue_closed) {
      lifecycleState.textContent = "RESOLVED";
      lifecycleState.className = "resolved";
      lifecycleCopy.textContent = "Verified PR published. Source issue removed from the Open Issues view.";
    } else if (job.pr_url) {
      lifecycleState.textContent = "PR OPEN";
      lifecycleState.className = "published";
      lifecycleCopy.textContent = "Verified pull request published; issue lifecycle action pending.";
    } else {
      lifecycleState.textContent = "OPEN";
      lifecycleState.className = "";
      lifecycleCopy.textContent = settings.close_issue === false
        ? "Automatic issue resolution disabled for this run."
        : "Close only after a verified pull request is published.";
    }
  }

  function resetTelemetry() {
    ["commit-base", "commit-test", "commit-fix"].forEach((id, index) => { $(id).textContent = index ? "pending" : "•••••••"; });
    $("runtime-languages").textContent = "profiling…";
    $("issue-lifecycle-state").textContent = "OPEN";
    $("issue-lifecycle-state").className = "";
    $("issue-lifecycle-copy").textContent = "Close only after a verified pull request is published.";
  }

  function renderInfrastructure() {
    const infrastructure = systemMetadata.infrastructure && typeof systemMetadata.infrastructure === "object" ? systemMetadata.infrastructure : {};
    const akash = infrastructure.akash && typeof infrastructure.akash === "object" ? infrastructure.akash : {};
    const x402 = infrastructure.x402 && typeof infrastructure.x402 === "object" ? infrastructure.x402 : {};
    const buildkite = infrastructure.buildkite && typeof infrastructure.buildkite === "object" ? infrastructure.buildkite : {};

    $("infra-akash-status").textContent = String(akash.status || "connected").toUpperCase();
    $("infra-akash-detail").textContent = akash.deployment || akash.label || "managed lease";
    $("infra-x402-status").textContent = String(x402.status || "demo bypass").toUpperCase();
    $("infra-x402-detail").textContent = `${x402.network || "Base"} · $${x402.price || "0.01"} USDC`;
    $("infra-buildkite-status").textContent = String(buildkite.status || "standby").toUpperCase();
    $("infra-buildkite-detail").textContent = `${buildkite.pipeline || "fixloop-verifier"} · ${buildkite.mode || systemMetadata.verifier || "local"}`;

    $("masthead-x402").textContent = x402.status === "enforced" ? "ENFORCED" : "BYPASS";
    $("masthead-akash").textContent = akash.status === "connected" ? "LEASED" : String(akash.status || "LIVE").toUpperCase();
    $("masthead-buildkite").textContent = buildkite.status === "online" ? "ONLINE" : "STANDBY";
    $("runtime-payment").textContent = `x402 · ${x402.status || "configured"}`;
    $("runtime-pipeline").textContent = buildkite.pipeline || "fixloop-verifier";
  }

  function renderVerdict(verdict, job, final) {
    const name = ["verified", "suspected_overfit", "rejected"].includes(verdict.verdict) ? verdict.verdict : "unknown";
    const copy = {
      verified: ["Verified", "The regression, full suite, and hidden challenges support this repair."],
      suspected_overfit: ["Suspected overfit", "Visible tests pass, but hidden evidence says the repair may not generalize."],
      rejected: ["Rejected", "The submission failed the proof contract. No pull request should open."],
      unknown: ["Verdict reported", `Unrecognized verifier outcome: ${String(verdict.verdict || "not reported")}.`],
    }[name];
    verdictPanel.dataset.verdict = name;
    verdictPanel.hidden = false;
    $("verdict-title").textContent = copy[0];
    $("verdict-summary").textContent = copy[1];
    $("raw-verdict-json").textContent = JSON.stringify(verdict, null, 2);
    const duration = $("verdict-duration");
    if (typeof verdict.duration_s === "number") { duration.querySelector("strong").textContent = `${verdict.duration_s}s`; duration.hidden = false; }
    else duration.hidden = true;
    renderReasons(verdict.reason_codes);
    renderTestEvidence(verdict);
    renderProbeEvidence(verdict.probes);
    renderOutcomeLinks(job, verdict, final);
  }

  function renderReasons(reasons) {
    const section = $("reason-section");
    const container = $("reason-codes");
    container.replaceChildren();
    const codes = Array.isArray(reasons) ? reasons.filter((code) => typeof code === "string") : [];
    section.hidden = codes.length === 0;
    codes.forEach((code) => { const chip = document.createElement("span"); chip.className = "reason-code"; chip.textContent = code; container.append(chip); });
  }

  function evidenceRow(label, status, detail) {
    const row = document.createElement("div"); row.className = `evidence-row ${status}`;
    const dot = document.createElement("i"); dot.className = "evidence-dot"; dot.setAttribute("aria-hidden", "true");
    const text = document.createElement("span"); text.textContent = label;
    const note = document.createElement("small"); note.textContent = detail;
    row.append(dot, text, note); return row;
  }

  function objectStatus(value, failureKey) {
    if (!value || typeof value !== "object") return ["neutral", "Not reported"];
    if (failureKey && Array.isArray(value[failureKey])) return value[failureKey].length ? ["fail", `${value[failureKey].length} found`] : ["pass", "Clear"];
    if (value.ok === true) return ["pass", value.code || "Passed"];
    if (value.ok === false) return ["fail", value.code || "Failed"];
    return ["neutral", value.code || "Reported"];
  }

  function renderTestEvidence(verdict) {
    const container = $("test-evidence"); container.replaceChildren();
    [["Commit structure", verdict.guards, "violations"], ["Fails before fix", verdict.fails_on_base], ["Passes twice", verdict.passes_on_fix], ["Zero suite regressions", verdict.suite, "regressions"]]
      .forEach(([label, value, failureKey]) => { const [status, detail] = objectStatus(value, failureKey); container.append(evidenceRow(label, status, detail)); });
  }

  function renderProbeEvidence(probes) {
    const container = $("probe-evidence"); container.replaceChildren();
    const safe = probes && typeof probes === "object" ? probes : {};
    const metamorphic = objectStatus(safe.metamorphic);
    container.append(evidenceRow("Metamorphic holdback", metamorphic[0], metamorphic[1]));
    const literal = safe.literal_scan;
    const status = literal && literal.suspicious ? "warn" : (literal ? "pass" : "neutral");
    const detail = literal ? (literal.suspicious ? `${Array.isArray(literal.hits) ? literal.hits.length : "?"} matches` : "Clear") : "Not reported";
    container.append(evidenceRow("Literal overfit scan", status, detail));
  }

  function isSafeLink(value) {
    if (typeof value !== "string") return false;
    try { return ["http:", "https:"].includes(new URL(value, window.location.origin).protocol); } catch (_) { return false; }
  }

  function addLink(container, href, label) {
    if (!isSafeLink(href)) return false;
    const link = document.createElement("a"); link.className = "outcome-link"; link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = `${label} ↗`; container.append(link); return true;
  }

  function renderOutcomeLinks(job, verdict, final) {
    const container = $("outcome-links"); container.replaceChildren();
    addLink(container, job.build_url || job.buildkite_url || verdict.build_url || verdict.buildkite_url, "Open verifier evidence");
    const hasPr = addLink(container, job.pr_url, "Open verified pull request");
    if (job.issue_closed) { const chip = document.createElement("span"); chip.className = "resolved-chip"; chip.textContent = "✓ Source issue resolved"; container.append(chip); }
    if (final && !hasPr) { const noPr = document.createElement("span"); noPr.className = "no-pr"; noPr.textContent = verdict.verdict === "verified" ? "No PR link reported" : "Verifier gate held · no PR opened"; container.append(noPr); }
  }

  async function loadSystemMetadata() {
    try {
      const response = await fetch("/system", { cache: "no-store" });
      if (response.ok) systemMetadata = await response.json();
      $("runtime-provider").textContent = systemMetadata.runtime || "Akash dCloud";
      $("runtime-verifier").textContent = systemMetadata.verifier || "configured";
      renderInfrastructure();
    } catch (_) {
      // Console still works against older deployments without /system.
    }
  }

  fixForm.addEventListener("submit", createFix);
  jobForm.addEventListener("submit", submitJobId);
  loadSystemMetadata();
})();

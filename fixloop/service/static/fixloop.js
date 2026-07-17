(() => {
  "use strict";

  const STAGES = ["queued", "clone", "agent", "verify", "pr", "finished"];
  const STAGE_ALIASES = {
    pending: "queued",
    accepted: "queued",
    cloning: "clone",
    checkout: "clone",
    cursor: "agent",
    coding: "agent",
    worker: "agent",
    testing: "verify",
    verifier: "verify",
    verification: "verify",
    buildkite: "verify",
    pull_request: "pr",
    opening_pr: "pr",
    pushing: "pr",
    done: "finished",
    complete: "finished",
    completed: "finished",
  };

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
      }
    } catch (_) {
      // Some x402 facilitators return an empty or encoded challenge body.
    }
    return response.statusText || `HTTP ${response.status}`;
  }

  async function createFix(event) {
    event.preventDefault();
    clearNotice();

    if (!fixForm.reportValidity()) return;

    const repo = $("repo-input").value.trim();
    const issue = Number.parseInt($("issue-input").value, 10);
    startButton.disabled = true;
    startButton.querySelector("span").textContent = "Starting…";

    try {
      const response = await fetch("/fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, issue }),
      });

      if (response.status === 402) {
        setNotice(
          "payment-required",
          "Payment required (HTTP 402)",
          "Complete this request with an x402-compatible CLI, then paste its returned job ID below. No job was started by this unpaid request."
        );
        return;
      }

      if (!response.ok) {
        throw new Error(await responseMessage(response));
      }

      const result = await response.json();
      if (!result || typeof result.job_id !== "string" || !result.job_id.trim()) {
        throw new Error("The service accepted the request but did not return a job ID.");
      }

      $("job-id-input").value = result.job_id.trim();
      setNotice("", "Job accepted", `Following job ${result.job_id.trim()} now.`);
      followJob(result.job_id.trim());
    } catch (error) {
      setNotice("", "Could not start the job", error.message || "The service did not respond.");
    } finally {
      startButton.disabled = false;
      startButton.querySelector("span").textContent = "Start verified fix";
    }
  }

  function submitJobId(event) {
    event.preventDefault();
    const jobId = $("job-id-input").value.trim();
    if (!jobId) {
      $("job-id-input").focus();
      return;
    }
    followJob(jobId);
  }

  function followJob(jobId) {
    currentJobId = jobId;
    window.clearTimeout(pollTimer);
    pollTimer = null;
    runConsole.hidden = false;
    verdictPanel.hidden = true;
    runError.hidden = true;
    runError.textContent = "";
    $("job-id-display").textContent = jobId;
    $("run-heading").textContent = "Verification in progress";
    setLiveState(true);
    updateStages("queued", null, false);
    runConsole.scrollIntoView({ behavior: "smooth", block: "start" });
    pollJob(jobId);
  }

  async function pollJob(jobId) {
    if (jobId !== currentJobId) return;

    try {
      const response = await fetch(`/job/${encodeURIComponent(jobId)}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(await responseMessage(response));

      const job = await response.json();
      if (!job || typeof job !== "object") throw new Error("The job response was not valid JSON.");
      if (jobId !== currentJobId) return;
      renderJob(job);

      if (!isFinished(job)) {
        pollTimer = window.setTimeout(() => pollJob(jobId), 1000);
      } else {
        setLiveState(false);
      }
    } catch (error) {
      setLiveState(false);
      runError.textContent = `Could not load job ${jobId}: ${error.message || "unknown error"}`;
      runError.hidden = false;
    }
  }

  function setLiveState(live) {
    const indicator = $("live-indicator");
    indicator.classList.toggle("stopped", !live);
    indicator.lastChild.textContent = live ? " Polling live" : " Polling stopped";
  }

  function renderJob(job) {
    const final = isFinished(job);
    const failed = Boolean(job.error);
    updateStages(job.stage, job, final, failed);
    $("updated-time").textContent = `Updated ${new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })}`;

    if (job.repo) {
      $("run-heading").textContent = failed
        ? "Run stopped"
        : (final ? "Evidence sealed" : "Verification in progress");
    }

    if (job.error) {
      runError.textContent = String(job.error);
      runError.hidden = false;
    } else {
      runError.hidden = true;
    }

    if (job.verdict && typeof job.verdict === "object") {
      renderVerdict(job.verdict, job, final);
    }
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
        if (itemStage === "queued") item.classList.add("complete");
        if (itemStage === "finished") item.classList.add("failed");
      } else if (itemStage === "pr" && skipPr) {
        item.classList.add("skipped");
      } else if (final || index < currentIndex) {
        item.classList.add("complete");
      } else if (index === currentIndex) {
        item.classList.add("active");
      }
    });

    const stageText = typeof rawStage === "string" && rawStage.trim() ? rawStage.trim() : "not reported";
    $("stage-description").textContent = failed
      ? "Run stopped: the backend reported an error"
      : (stage
        ? `Current stage: ${stageText}`
        : `Backend stage: ${stageText} (waiting for the next known checkpoint)`);
  }

  function renderVerdict(verdict, job, final) {
    const name = ["verified", "suspected_overfit", "rejected"].includes(verdict.verdict)
      ? verdict.verdict
      : "unknown";
    const copy = {
      verified: {
        title: "Verified",
        summary: "The regression test, full suite, and held-back challenges support this fix.",
      },
      suspected_overfit: {
        title: "Suspected overfit",
        summary: "The visible repro passed, but hidden evidence suggests the fix does not generalize.",
      },
      rejected: {
        title: "Rejected",
        summary: "The submission did not satisfy the verifier contract. No pull request should open.",
      },
      unknown: {
        title: "Verdict reported",
        summary: `The verifier returned an unrecognized outcome: ${String(verdict.verdict || "not reported")}.`,
      },
    }[name];

    verdictPanel.dataset.verdict = name;
    verdictPanel.hidden = false;
    $("verdict-title").textContent = copy.title;
    $("verdict-summary").textContent = copy.summary;
    $("raw-verdict-json").textContent = JSON.stringify(verdict, null, 2);

    const duration = $("verdict-duration");
    if (typeof verdict.duration_s === "number") {
      duration.querySelector("strong").textContent = `${verdict.duration_s}s`;
      duration.hidden = false;
    } else {
      duration.hidden = true;
    }

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
    codes.forEach((code) => {
      const chip = document.createElement("span");
      chip.className = "reason-code";
      chip.textContent = code;
      container.append(chip);
    });
  }

  function evidenceRow(label, status, detail) {
    const row = document.createElement("div");
    row.className = `evidence-row ${status}`;
    const dot = document.createElement("i");
    dot.className = "evidence-dot";
    dot.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = label;
    const note = document.createElement("small");
    note.textContent = detail;
    row.append(dot, text, note);
    return row;
  }

  function objectStatus(value, failureKey) {
    if (!value || typeof value !== "object") return ["neutral", "Not reported"];
    if (failureKey && Array.isArray(value[failureKey])) {
      return value[failureKey].length ? ["fail", `${value[failureKey].length} found`] : ["pass", "Clear"];
    }
    if (value.ok === true) return ["pass", value.code || "Passed"];
    if (value.ok === false) return ["fail", value.code || "Failed"];
    return ["neutral", value.code || "Reported"];
  }

  function renderTestEvidence(verdict) {
    const container = $("test-evidence");
    container.replaceChildren();
    const checks = [
      ["Submission structure", verdict.guards, "violations"],
      ["Regression fails before fix", verdict.fails_on_base],
      ["Regression passes twice", verdict.passes_on_fix],
      ["Full suite has no regressions", verdict.suite, "regressions"],
    ];
    checks.forEach(([label, value, failureKey]) => {
      const [status, detail] = objectStatus(value, failureKey);
      container.append(evidenceRow(label, status, detail));
    });
  }

  function renderProbeEvidence(probes) {
    const container = $("probe-evidence");
    container.replaceChildren();
    const safeProbes = probes && typeof probes === "object" ? probes : {};
    const metamorphic = objectStatus(safeProbes.metamorphic);
    container.append(evidenceRow("Metamorphic holdback", metamorphic[0], metamorphic[1]));

    const literal = safeProbes.literal_scan;
    let literalStatus = "neutral";
    let literalDetail = "Not reported";
    if (literal && typeof literal === "object") {
      literalStatus = literal.suspicious ? "warn" : "pass";
      const matches = Array.isArray(literal.hits) ? literal.hits.length : null;
      literalDetail = literal.suspicious
        ? (matches === null ? "Suspicious" : `${matches} match${matches === 1 ? "" : "es"}`)
        : "Clear";
    }
    container.append(evidenceRow("Literal overfit scan", literalStatus, literalDetail));
  }

  function isSafeLink(value) {
    if (typeof value !== "string") return false;
    try {
      const url = new URL(value, window.location.origin);
      return url.protocol === "http:" || url.protocol === "https:";
    } catch (_) {
      return false;
    }
  }

  function addLink(container, href, label) {
    if (!isSafeLink(href)) return false;
    const link = document.createElement("a");
    link.className = "outcome-link";
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${label} ↗`;
    container.append(link);
    return true;
  }

  function renderOutcomeLinks(job, verdict, final) {
    const container = $("outcome-links");
    container.replaceChildren();
    const buildUrl = job.build_url || job.buildkite_url || verdict.build_url || verdict.buildkite_url;
    addLink(container, buildUrl, "Open Buildkite evidence");
    const hasPr = addLink(container, job.pr_url, "Open verified pull request");

    if (final && !hasPr) {
      const noPr = document.createElement("span");
      noPr.className = "no-pr";
      noPr.textContent = verdict.verdict === "verified"
        ? "No pull request link reported"
        : "No pull request opened — verifier gate held";
      container.append(noPr);
    }
  }

  fixForm.addEventListener("submit", createFix);
  jobForm.addEventListener("submit", submitJobId);
})();

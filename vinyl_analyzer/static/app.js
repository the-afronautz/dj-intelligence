/* Vinyl Analyzer — frontend logic.
 *
 *  Records from the default microphone via MediaRecorder, sends the blob
 *  to the Flask backend for analysis, and renders results + library.
 */

(() => {
  const recordBtn = document.getElementById("recordBtn");
  const recordLabel = recordBtn.querySelector(".label");
  const timerEl = document.getElementById("timer");
  const statusEl = document.getElementById("status");
  const meter = document.getElementById("meter");
  const meterCtx = meter.getContext("2d");

  const resultCard = document.getElementById("resultCard");
  const resBpm = document.getElementById("resBpm");
  const resBpmConf = document.getElementById("resBpmConf");
  const resCamelot = document.getElementById("resCamelot");
  const resKey = document.getElementById("resKey");
  const resKeyConf = document.getElementById("resKeyConf");
  const resDuration = document.getElementById("resDuration");

  const altPanel = document.getElementById("altPanel");
  const altPrimaryCamelot = document.getElementById("altPrimaryCamelot");
  const altPrimaryKey = document.getElementById("altPrimaryKey");
  const altPrimaryShare = document.getElementById("altPrimaryShare");
  const altAltCamelot = document.getElementById("altAltCamelot");
  const altAltKey = document.getElementById("altAltKey");
  const altAltShare = document.getElementById("altAltShare");
  const altPickPrimary = document.getElementById("altPickPrimary");
  const altPickAlt = document.getElementById("altPickAlt");

  const bpmAltPanel = document.getElementById("bpmAltPanel");
  const bpmCandidatesEl = document.getElementById("bpmCandidates");
  const tapBtn = document.getElementById("tapBtn");
  const tapMeta = document.getElementById("tapMeta");
  const manualBpm = document.getElementById("manualBpm");

  const saveForm = document.getElementById("saveForm");
  const trackNameInput = document.getElementById("trackName");
  const artistInput = document.getElementById("artist");
  const notesInput = document.getElementById("notes");
  const discardBtn = document.getElementById("discardBtn");

  const searchBox = document.getElementById("searchBox");
  const tracksBody = document.getElementById("tracksBody");
  const emptyState = document.getElementById("emptyState");

  // Recorder state
  let mediaStream = null;
  let mediaRecorder = null;
  let audioChunks = [];
  let recordStart = 0;
  let recordTimerInterval = null;
  let autoStopTimeout = null;
  let analyser = null;
  let meterRAF = 0;

  let pendingResult = null;
  let userKeyChoice = "primary"; // "primary" or "alt"
  let userBpmOverride = null;    // numeric BPM if user picked / tapped / typed
  let userBpmSource = null;      // "candidate" | "tap" | "manual"
  let tapTimestamps = [];
  let tapResetTimer = 0;

  // Library state (for sortable header)
  let sortKey = "captured_at";
  let sortOrder = "desc";

  // ---------- Recording ----------
  recordBtn.addEventListener("click", async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      stopRecording();
    } else {
      await startRecording();
    }
  });

  async function startRecording() {
    setStatus("Requesting microphone…", "busy");
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
    } catch (err) {
      setStatus("Microphone access denied. Enable mic permission for this site and reload.", "error");
      return;
    }

    setupMeter(mediaStream);

    // Prefer webm/opus where supported; the backend handles either.
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : (MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "");
    try {
      mediaRecorder = mime ? new MediaRecorder(mediaStream, { mimeType: mime })
                           : new MediaRecorder(mediaStream);
    } catch (err) {
      setStatus("This browser can't record audio. Try Chrome or Safari.", "error");
      return;
    }
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) audioChunks.push(e.data); };
    mediaRecorder.onstop = handleRecordingStop;
    mediaRecorder.start();

    recordStart = performance.now();
    recordTimerInterval = setInterval(updateTimer, 100);
    autoStopTimeout = setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state === "recording") stopRecording();
    }, 30_000);

    recordBtn.classList.add("recording");
    recordLabel.textContent = "Stop recording";
    setStatus("Recording — 30s will auto-stop. Play your record now.", "");
  }

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
    }
    if (autoStopTimeout) { clearTimeout(autoStopTimeout); autoStopTimeout = null; }
    if (recordTimerInterval) { clearInterval(recordTimerInterval); recordTimerInterval = null; }
    recordBtn.classList.remove("recording");
    recordLabel.textContent = "Start recording";
  }

  async function handleRecordingStop() {
    teardownMeter();
    if (mediaStream) {
      mediaStream.getTracks().forEach(t => t.stop());
      mediaStream = null;
    }
    if (!audioChunks.length) {
      setStatus("No audio captured. Try again.", "error");
      return;
    }
    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    setStatus(`Analyzing ${(blob.size/1024).toFixed(0)} KB clip…`, "busy");

    const form = new FormData();
    const filename = `snippet.${(mediaRecorder.mimeType || "").includes("ogg") ? "ogg" : "webm"}`;
    form.append("audio", blob, filename);
    try {
      const r = await fetch("/api/analyze", { method: "POST", body: form });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: r.statusText }));
        throw new Error(err.error || `HTTP ${r.status}`);
      }
      const data = await r.json();
      pendingResult = data;
      showResult(data);
      setStatus("Analysis ready — add track info and save.", "");
    } catch (err) {
      setStatus(`Analysis failed: ${err.message}`, "error");
    }
  }

  function updateTimer() {
    const elapsed = (performance.now() - recordStart) / 1000;
    const m = Math.floor(elapsed / 60).toString().padStart(2, "0");
    const s = Math.floor(elapsed % 60).toString().padStart(2, "0");
    timerEl.textContent = `${m}:${s}`;
  }

  function setStatus(msg, level) {
    statusEl.textContent = msg || "";
    statusEl.className = "status" + (level ? " " + level : "");
  }

  // ---------- VU meter ----------
  function setupMeter(stream) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);

    const draw = () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (let i = 0; i < data.length; i++) {
        const v = Math.abs(data[i] - 128) / 128;
        if (v > peak) peak = v;
      }
      meterCtx.clearRect(0, 0, meter.width, meter.height);
      // Background bar
      meterCtx.fillStyle = "#1d222c";
      meterCtx.fillRect(0, 0, meter.width, meter.height);
      // Level
      const w = meter.width * Math.min(1, peak * 1.5);
      const grad = meterCtx.createLinearGradient(0, 0, meter.width, 0);
      grad.addColorStop(0, "#58d68d");
      grad.addColorStop(0.7, "#f4c430");
      grad.addColorStop(1, "#ff5d6c");
      meterCtx.fillStyle = grad;
      meterCtx.fillRect(0, 0, w, meter.height);
      meterRAF = requestAnimationFrame(draw);
    };
    draw();
  }
  function teardownMeter() {
    if (meterRAF) cancelAnimationFrame(meterRAF);
    meterRAF = 0;
    analyser = null;
    meterCtx.clearRect(0, 0, meter.width, meter.height);
    meterCtx.fillStyle = "#1d222c";
    meterCtx.fillRect(0, 0, meter.width, meter.height);
  }

  // ---------- Result + save ----------
  function showResult(data) {
    resBpm.textContent = data.bpm?.toFixed(1) ?? "—";
    resBpmConf.textContent = `Confidence ${pctOrDash(data.bpm_confidence)}`;
    resCamelot.textContent = data.camelot ?? "—";
    resKey.textContent = `Key: ${data.key ?? "—"}`;
    resKeyConf.textContent = pctOrDash(data.key_confidence);
    resDuration.textContent = `Duration: ${data.duration_seconds?.toFixed(1) ?? "—"}s`;
    resultCard.classList.remove("hidden");

    // Show alt panel if key confidence < 60% and there's a runner-up
    userKeyChoice = "primary";
    altPickPrimary.classList.add("selected");
    altPickAlt.classList.remove("selected");
    if (data.key_confidence != null && data.key_confidence < 0.6 && data.alt_camelot) {
      altPrimaryCamelot.textContent = data.camelot ?? "—";
      altPrimaryKey.textContent = data.key ?? "—";
      altPrimaryShare.textContent = pctOrDash(data.key_confidence);
      altAltCamelot.textContent = data.alt_camelot ?? "—";
      altAltKey.textContent = data.alt_key ?? "—";
      altAltShare.textContent = pctOrDash(data.alt_share);
      altPanel.classList.remove("hidden");
    } else {
      altPanel.classList.add("hidden");
    }

    // Reset BPM override state and render candidates
    userBpmOverride = null;
    userBpmSource = null;
    manualBpm.value = "";
    tapTimestamps = [];
    tapMeta.textContent = "Tap on the beat (≥4 taps)";
    renderBpmCandidates(data.bpm_candidates || [], data.bpm);

    trackNameInput.value = "";
    artistInput.value = "";
    notesInput.value = "";
    trackNameInput.focus();
  }

  function renderBpmCandidates(candidates, primaryBpm) {
    bpmCandidatesEl.innerHTML = "";
    // Always show at least 2 options when we have them; surface up to 3.
    const show = (candidates || []).slice(0, 3);
    if (show.length < 2) {
      bpmAltPanel.classList.add("hidden");
      return;
    }
    bpmAltPanel.classList.remove("hidden");

    show.forEach((c, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "alt-pick";
      if (idx === 0) btn.classList.add("selected");
      btn.dataset.bpm = c.bpm;
      btn.dataset.score = c.score;
      btn.dataset.source = c.source;
      btn.innerHTML = `
        <span class="alt-bpm-value">${Number(c.bpm).toFixed(1)} BPM</span>
        <span class="alt-bpm-meta">Score ${Math.round((c.score || 0) * 100)}% · vote ${Math.round((c.share || 0) * 100)}%</span>
        <span class="alt-bpm-source">${c.source === "octave_alt" ? "Half/double-time" : "Detected"}</span>
      `;
      btn.addEventListener("click", () => {
        document.querySelectorAll("#bpmCandidates .alt-pick").forEach(b => b.classList.remove("selected"));
        btn.classList.add("selected");
        const v = parseFloat(btn.dataset.bpm);
        userBpmOverride = v;
        userBpmSource = "candidate";
        resBpm.textContent = v.toFixed(1);
        resBpmConf.textContent = `Picked candidate (was ${Number(primaryBpm).toFixed(1)})`;
        // Clear other overrides so this one wins
        manualBpm.value = "";
        tapTimestamps = [];
        tapMeta.textContent = "Tap on the beat (≥4 taps)";
      });
      bpmCandidatesEl.appendChild(btn);
    });
  }

  // --- Tap tempo ---
  tapBtn.addEventListener("click", () => {
    const now = performance.now();
    // Reset if there's been a long pause between taps (>2s)
    if (tapTimestamps.length && now - tapTimestamps[tapTimestamps.length - 1] > 2000) {
      tapTimestamps = [];
    }
    tapTimestamps.push(now);
    tapBtn.classList.add("tapping");
    clearTimeout(tapResetTimer);
    tapResetTimer = setTimeout(() => tapBtn.classList.remove("tapping"), 120);

    if (tapTimestamps.length < 2) {
      tapMeta.textContent = `Tapped 1 · need ≥4`;
      return;
    }
    // Compute BPM from a rolling window of the most recent taps (max 8)
    const recent = tapTimestamps.slice(-8);
    const ibis = [];
    for (let i = 1; i < recent.length; i++) ibis.push(recent[i] - recent[i - 1]);
    const meanIbiMs = ibis.reduce((a, b) => a + b, 0) / ibis.length;
    const bpm = 60000 / meanIbiMs;

    if (tapTimestamps.length >= 4) {
      userBpmOverride = bpm;
      userBpmSource = "tap";
      resBpm.textContent = bpm.toFixed(1);
      resBpmConf.textContent = `Tapped (${tapTimestamps.length} taps)`;
      tapMeta.textContent = `Tapped ${tapTimestamps.length} · ${bpm.toFixed(1)} BPM (keep tapping to refine)`;
      // Clear other overrides
      manualBpm.value = "";
      document.querySelectorAll("#bpmCandidates .alt-pick").forEach(b => b.classList.remove("selected"));
    } else {
      tapMeta.textContent = `Tapped ${tapTimestamps.length} · ~${bpm.toFixed(1)} BPM (need ≥4)`;
    }
  });

  // --- Manual override ---
  manualBpm.addEventListener("input", () => {
    const v = parseFloat(manualBpm.value);
    if (isNaN(v) || v <= 0) {
      // If field cleared, revert to whatever was previously chosen
      if (userBpmSource === "manual") {
        userBpmOverride = null;
        userBpmSource = null;
        if (pendingResult) {
          resBpm.textContent = pendingResult.bpm.toFixed(1);
          resBpmConf.textContent = `Confidence ${pctOrDash(pendingResult.bpm_confidence)}`;
        }
      }
      return;
    }
    userBpmOverride = v;
    userBpmSource = "manual";
    resBpm.textContent = v.toFixed(1);
    resBpmConf.textContent = "Manual override";
    // Clear other overrides
    tapTimestamps = [];
    tapMeta.textContent = "Tap on the beat (≥4 taps)";
    document.querySelectorAll("#bpmCandidates .alt-pick").forEach(b => b.classList.remove("selected"));
  });

  altPickPrimary.addEventListener("click", () => {
    userKeyChoice = "primary";
    altPickPrimary.classList.add("selected");
    altPickAlt.classList.remove("selected");
    if (pendingResult) {
      resCamelot.textContent = pendingResult.camelot ?? "—";
      resKey.textContent = `Key: ${pendingResult.key ?? "—"}`;
    }
  });
  altPickAlt.addEventListener("click", () => {
    if (!pendingResult || !pendingResult.alt_camelot) return;
    userKeyChoice = "alt";
    altPickAlt.classList.add("selected");
    altPickPrimary.classList.remove("selected");
    resCamelot.textContent = pendingResult.alt_camelot;
    resKey.textContent = `Key: ${pendingResult.alt_key}`;
  });

  function pctOrDash(v) {
    if (v == null || isNaN(v)) return "—";
    return `${Math.round(v * 100)}%`;
  }

  saveForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!pendingResult) return;

    // Build the row; if the user picked the alt key, swap primary <-> alt
    // so the saved row reflects their choice and the runner-up is remembered
    // as the alternative.
    let row = { ...pendingResult };
    if (userKeyChoice === "alt" && pendingResult.alt_camelot) {
      row.key = pendingResult.alt_key;
      row.camelot = pendingResult.alt_camelot;
      row.key_confidence = pendingResult.alt_share;
      row.alt_key = pendingResult.key;
      row.alt_camelot = pendingResult.camelot;
      row.alt_share = pendingResult.key_confidence;
    }

    // If the user overrode BPM (candidate pick / tap / manual), put their
    // value in `bpm` and demote the original detection to `alt_bpm`.
    if (userBpmOverride && userBpmOverride > 0) {
      row.alt_bpm = pendingResult.bpm;
      row.alt_bpm_share = pendingResult.bpm_confidence;
      row.bpm = Number(userBpmOverride.toFixed(2));
      // Manual/tap overrides imply user-asserted ground truth → max conf.
      // Candidate picks inherit the candidate's score if we have it.
      row.bpm_confidence = (userBpmSource === "manual" || userBpmSource === "tap") ? 1.0 : pendingResult.bpm_confidence;
      // Annotate notes
      const tag = userBpmSource === "manual" ? "manual BPM"
                : userBpmSource === "tap" ? "tapped BPM"
                : "picked BPM candidate";
      const existingNotes = notesInput.value.trim();
      row.__bpm_note = `[${tag}: orig ${pendingResult.bpm.toFixed(1)}]` + (existingNotes ? ` ${existingNotes}` : "");
    }

    const body = {
      ...row,
      captured_at: new Date().toISOString(),
      track_name: trackNameInput.value.trim(),
      artist: artistInput.value.trim(),
      notes: row.__bpm_note || notesInput.value.trim(),
    };
    delete body.__bpm_note;
    const r = await fetch("/api/tracks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      setStatus("Save failed.", "error");
      return;
    }
    pendingResult = null;
    resultCard.classList.add("hidden");
    setStatus("Saved to library.", "");
    timerEl.textContent = "00:00";
    loadTracks();
  });

  discardBtn.addEventListener("click", () => {
    pendingResult = null;
    altPanel.classList.add("hidden");
    bpmAltPanel.classList.add("hidden");
    bpmCandidatesEl.innerHTML = "";
    manualBpm.value = "";
    tapTimestamps = [];
    tapMeta.textContent = "Tap on the beat (≥4 taps)";
    userBpmOverride = null;
    userBpmSource = null;
    resultCard.classList.add("hidden");
    setStatus("Discarded.", "");
  });

  // ---------- Library ----------
  async function loadTracks() {
    const q = searchBox.value.trim();
    const params = new URLSearchParams({ sort: sortKey, order: sortOrder });
    if (q) params.set("q", q);
    const r = await fetch(`/api/tracks?${params}`);
    if (!r.ok) return;
    const rows = await r.json();
    renderTracks(rows);
  }

  function renderTracks(rows) {
    tracksBody.innerHTML = "";
    if (!rows.length) {
      emptyState.style.display = "block";
    } else {
      emptyState.style.display = "none";
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${formatDate(row.captured_at)}</td>
        <td>${escapeHtml(row.track_name || "—")}</td>
        <td>${escapeHtml(row.artist || "—")}</td>
        <td class="bpm">${row.bpm != null ? row.bpm.toFixed(1) : "—"}${confPill(row.bpm_confidence)}</td>
        <td class="camelot">${escapeHtml(row.camelot || "—")}</td>
        <td>${escapeHtml(row.key || "—")}${confPill(row.key_confidence)}</td>
        <td>${pctOrDash(avgConf(row.bpm_confidence, row.key_confidence))}</td>
        <td class="notes">${escapeHtml(row.notes || "")}</td>
        <td><button class="delete" title="Delete" data-id="${row.id}">✕</button></td>
      `;
      tracksBody.appendChild(tr);
    }
    updateSortIndicators();
  }

  function avgConf(a, b) {
    const xs = [a, b].filter(v => v != null);
    if (!xs.length) return null;
    return xs.reduce((s, v) => s + v, 0) / xs.length;
  }

  function confPill(v) {
    if (v == null) return "";
    const pct = Math.round(v * 100);
    let cls = "bad";
    if (pct >= 70) cls = "good";
    else if (pct >= 40) cls = "warn";
    return ` <span class="conf-pill ${cls}">${pct}%</span>`;
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  tracksBody.addEventListener("click", async (e) => {
    const btn = e.target.closest(".delete");
    if (!btn) return;
    const id = btn.dataset.id;
    if (!confirm("Delete this entry?")) return;
    const r = await fetch(`/api/tracks/${id}`, { method: "DELETE" });
    if (r.ok) loadTracks();
  });

  document.querySelectorAll("th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortOrder = sortOrder === "asc" ? "desc" : "asc";
      else { sortKey = k; sortOrder = "asc"; }
      loadTracks();
    });
  });

  function updateSortIndicators() {
    document.querySelectorAll("th.sortable").forEach(th => {
      th.classList.remove("sort-asc", "sort-desc");
      if (th.dataset.sort === sortKey) {
        th.classList.add(sortOrder === "asc" ? "sort-asc" : "sort-desc");
      }
    });
  }

  let searchDebounce = 0;
  searchBox.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(loadTracks, 180);
  });

  // Initial load
  loadTracks();
})();

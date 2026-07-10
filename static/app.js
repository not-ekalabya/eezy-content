const $ = (id) => document.getElementById(id);
const form = $("searchForm");
const grid = $("grid");
const statusEl = $("status");
const titleEl = $("resultsTitle");
const tracePanel = $("tracePanel");
const trace = $("trace");
const traceSummary = $("traceSummary");
const deepChip = $("deepChip");

let eventSource = null;
let currentType = "";

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (!q) { loadBrowse(); return; }
  document.body.classList.add("searched");
  if (deepChip.classList.contains("active")) deepSearch(q);
  else instantSearch(q);
});

deepChip.addEventListener("click", () => deepChip.classList.toggle("active"));

document.querySelectorAll(".type-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelector(".type-chip.active")?.classList.remove("active");
    chip.classList.add("active");
    currentType = chip.dataset.type;
    const q = $("q").value.trim();
    if (q) instantSearch(q);
    else loadBrowse();
  });
});

window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    $("q").focus();
    $("q").select();
  }
});

const placeholders = [
  "Describe the memory you are looking for…",
  "man in red jacket falls off bike near beach…",
  "golden hour portraits in a field…",
  "the clip where someone says “launch day”…",
  "whiteboard covered in architecture diagrams…",
];
let phIdx = 0;
setInterval(() => {
  const q = $("q");
  if (document.activeElement === q || q.value) return;
  phIdx = (phIdx + 1) % placeholders.length;
  q.placeholder = placeholders[phIdx];
}, 4000);

function setStatus(text, isError = false) {
  statusEl.textContent = text || " ";
  statusEl.classList.toggle("error", isError);
}

async function loadBrowse() {
  closeStream();
  tracePanel.classList.add("hidden");
  document.body.classList.remove("searched");
  titleEl.textContent = "Library";
  setStatus("loading library…");
  grid.innerHTML = "";
  try {
    const res = await fetch(`/api/browse${currentType ? `?type=${currentType}` : ""}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderResults(data.results);
    setStatus(`library — ${data.results.length} items`);
  } catch (err) {
    setStatus(`error: ${err.message}`, true);
  }
}

loadBrowse();

async function instantSearch(q) {
  closeStream();
  tracePanel.classList.add("hidden");
  titleEl.textContent = "Discovery Grid";
  setStatus("searching…");
  grid.innerHTML = "";
  try {
    const url = `/api/search?q=${encodeURIComponent(q)}${currentType ? `&type=${currentType}` : ""}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderResults(data.results);
    setStatus(`results for “${q}” — ${data.results.length} items`);
  } catch (err) {
    setStatus(`error: ${err.message}`, true);
  }
}

function deepSearch(q) {
  closeStream();
  grid.innerHTML = "";
  trace.innerHTML = "";
  traceSummary.textContent = "";
  tracePanel.classList.remove("hidden");
  const header = tracePanel.querySelector(".trace-header");
  header.classList.remove("done");
  header.querySelector(".trace-state").textContent = "agent working…";
  titleEl.textContent = "Discovery Grid";
  setStatus(`deep search — “${q}”`);

  // run instant search immediately as first-pass results
  instantFirstPass(q);

  eventSource = new EventSource(`/api/deep?q=${encodeURIComponent(q)}`);
  eventSource.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "text") addTrace(esc(ev.text), "agent-text");
    else if (ev.type === "tool") addTrace(`<span class="tool-name">${esc(ev.name)}</span> ${esc(ev.input)}`);
    else if (ev.type === "error") {
      addTrace(`error: ${esc(ev.message)}`);
      finishTrace("agent failed — showing instant results");
    } else if (ev.type === "results") {
      renderResults(ev.segments);
      finishTrace(ev.summary || "done");
      setStatus(`results for “${q}” — ${ev.segments.length} items` +
        (ev.enriched ? ` · ${ev.enriched} newly enriched` : ""));
    } else if (ev.type === "done") closeStream();
  };
  eventSource.onerror = () => { finishTrace("connection closed"); closeStream(); };
}

async function instantFirstPass(q) {
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&k=12`);
    if (res.ok) {
      const data = await res.json();
      if (!grid.children.length) {
        renderResults(data.results);
        setStatus("instant results — agent refining…");
      }
    }
  } catch { /* agent results will land anyway */ }
}

function addTrace(html, cls = "") {
  const div = document.createElement("div");
  div.className = `trace-line ${cls}`;
  div.innerHTML = html;
  trace.appendChild(div);
  trace.scrollTop = trace.scrollHeight;
}

function finishTrace(summary) {
  const header = tracePanel.querySelector(".trace-header");
  header.classList.add("done");
  header.querySelector(".trace-state").textContent = "agent done";
  traceSummary.textContent = summary || "";
}

function closeStream() {
  if (eventSource) { eventSource.close(); eventSource = null; }
}

function renderResults(items) {
  grid.innerHTML = "";
  for (const it of items) grid.appendChild(card(it));
}

function splitTags(tags) {
  return String(tags || "").split(",").map((t) => t.trim()).filter(Boolean);
}

function card(it) {
  const el = document.createElement("div");
  el.className = "masonry-item";
  const isVideo = it.type === "video";
  const tags = splitTags(it.tags);
  const captionText = it.caption || (it.transcript ? `“${it.transcript}”` : "not yet enriched");
  el.innerHTML = `
    <img src="${it.thumb}" loading="lazy" alt="" />
    ${isVideo ? `<span class="badge video">video</span>` : ""}
    ${isVideo ? `<span class="timecode">${it.duration != null ? fmtTime(it.duration) + (it.shots > 1 ? ` · ${it.shots} shots` : "") : fmtTime(it.t_start)}</span>` : ""}
    <div class="overlay">
      <div class="ov-caption ${it.caption || it.transcript ? "" : "empty"}">${esc(captionText)}</div>
      ${tags.length ? `<div class="ov-tags">${tags.slice(0, 4).map((t) => `<span class="ov-tag">${esc(t)}</span>`).join("")}</div>` : ""}
    </div>`;
  el.addEventListener("click", () => openViewer(it));
  return el;
}

function openViewer(it) {
  const media = $("viewerMedia");
  const meta = $("viewerMeta");
  const isVideo = it.type === "video";
  if (isVideo) {
    media.innerHTML = `<video controls autoplay src="${it.media}"></video>`;
    const v = media.querySelector("video");
    v.addEventListener("loadedmetadata", () => { v.currentTime = it.t_start; }, { once: true });
  } else {
    media.innerHTML = `<img src="${it.media}" alt="" />`;
  }
  const tags = splitTags(it.tags);
  const sections = [];
  sections.push(`
    <div class="meta-section">
      <span class="meta-label">${isVideo ? "video segment" : "photo"}</span>
      ${isVideo ? `<span class="mono-chip">${fmtTime(it.t_start)} – ${fmtTime(it.t_end)}</span>` : ""}
    </div>`);
  if (it.caption) sections.push(`
    <div class="meta-section">
      <span class="meta-label">AI caption</span>
      <p>${esc(it.caption)}</p>
    </div>`);
  if (it.transcript) sections.push(`
    <div class="meta-section">
      <span class="meta-label">transcript</span>
      <p class="transcript">“${esc(it.transcript)}”</p>
    </div>`);
  if (tags.length) sections.push(`
    <div class="meta-section">
      <span class="meta-label">semantic links</span>
      <div class="ai-tags">${tags.map((t) => `<span class="ai-tag">${esc(t)}</span>`).join("")}</div>
    </div>`);
  if (!it.caption && !it.transcript && !tags.length) sections.push(`
    <div class="meta-section">
      <span class="meta-label">curation notes</span>
      <p class="empty">Not yet enriched — run a deep search and the agent will describe it.</p>
    </div>`);
  meta.innerHTML = sections.join("");
  $("viewer").classList.remove("hidden");
}

function closeViewer() {
  $("viewerMedia").innerHTML = "";
  $("viewer").classList.add("hidden");
}
$("viewerClose").addEventListener("click", closeViewer);
document.querySelector(".viewer-backdrop").addEventListener("click", closeViewer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeViewer(); closeFolders(); }
});

// ---------- synced folders ----------

const foldersModal = $("foldersModal");
const folderList = $("folderList");
const folderError = $("folderError");
const syncStatusEl = $("syncStatus");
let statusPoll = null;
let lastSyncSeen = null;

$("foldersBtn").addEventListener("click", openFolders);
$("foldersClose").addEventListener("click", closeFolders);
foldersModal.querySelector(".modal-backdrop").addEventListener("click", closeFolders);

function openFolders() {
  foldersModal.classList.remove("hidden");
  folderError.textContent = "";
  refreshFolders();
  statusPoll = setInterval(refreshFolders, 2000);
}

function closeFolders() {
  foldersModal.classList.add("hidden");
  if (statusPoll) { clearInterval(statusPoll); statusPoll = null; }
}

async function refreshFolders() {
  try {
    const res = await fetch("/api/folders");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    renderFolders(data.folders);
    renderSyncStatus(data.status);
    maybeRefreshGrid(data.status);
  } catch (err) {
    syncStatusEl.textContent = `error: ${err.message}`;
    syncStatusEl.classList.add("error");
  }
}

function renderFolders(folders) {
  if (!folders.length) {
    folderList.innerHTML = `<li class="folder-empty">No folders watched yet — add one above.</li>`;
    return;
  }
  folderList.innerHTML = folders.map((f) => `
    <li>
      <span class="material-symbols-outlined">folder</span>
      <span class="folder-path" title="${esc(f.path)}">${esc(f.path)}</span>
      ${f.exists ? "" : `<span class="folder-missing">missing</span>`}
      <button class="folder-remove" data-path="${esc(f.path)}" aria-label="Remove">
        <span class="material-symbols-outlined">close</span>
      </button>
    </li>`).join("");
  folderList.querySelectorAll(".folder-remove").forEach((btn) => {
    btn.addEventListener("click", () => removeFolder(btn.dataset.path));
  });
}

function renderSyncStatus(st) {
  syncStatusEl.classList.remove("error");
  if (st.running) {
    syncStatusEl.innerHTML = `<span class="pulse"></span> syncing…`;
  } else if (st.last_error) {
    syncStatusEl.textContent = `sync error: ${st.last_error}`;
    syncStatusEl.classList.add("error");
  } else if (st.last_sync) {
    const t = new Date(st.last_sync * 1000).toLocaleTimeString();
    syncStatusEl.textContent = `last sync ${t} · +${st.last_added} / −${st.last_removed}`;
  } else {
    syncStatusEl.textContent = "not synced yet";
  }
  $("syncNow").disabled = !!st.running;
}

function maybeRefreshGrid(st) {
  if (st.running || !st.last_sync) return;
  if (lastSyncSeen === null) { lastSyncSeen = st.last_sync; return; }
  if (st.last_sync !== lastSyncSeen) {
    lastSyncSeen = st.last_sync;
    if ((st.last_added || st.last_removed) && !$("q").value.trim()) loadBrowse();
  }
}

$("folderAddForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const path = $("folderPath").value.trim();
  if (!path) return;
  folderError.textContent = "";
  try {
    const res = await fetch("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))).detail;
      throw new Error(detail || `HTTP ${res.status}`);
    }
    $("folderPath").value = "";
    const data = await res.json();
    renderFolders(data.folders);
    renderSyncStatus(data.status);
  } catch (err) {
    folderError.textContent = err.message;
  }
});

async function removeFolder(path) {
  try {
    const res = await fetch(`/api/folders?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderFolders(data.folders);
  } catch (err) {
    folderError.textContent = err.message;
  }
}

$("syncNow").addEventListener("click", async () => {
  await fetch("/api/sync", { method: "POST" });
  refreshFolders();
});

// background: pick up sync results even with the modal closed
setInterval(async () => {
  if (statusPoll) return; // modal open — already polling
  try {
    const res = await fetch("/api/folders");
    if (res.ok) maybeRefreshGrid((await res.json()).status);
  } catch { /* server briefly unreachable */ }
}, 20000);

function fmtTime(s) {
  s = Math.floor(s || 0);
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

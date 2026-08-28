"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const api = async (path, opts = {}) => {
  const o = { headers: {}, ...opts };
  if (o.body && typeof o.body !== "string") { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
  const r = await fetch(path, o);
  const txt = await r.text();
  let d = {};
  try { d = txt ? JSON.parse(txt) : {}; } catch { throw new Error(`Сервер вернул ошибку (HTTP ${r.status}). Перезапустите студию (LocalTTS.bat).`); }
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
};

let INFO = null;

function banner(msg, kind) { const b = $("banner"); if (!msg) { b.className = "banner hidden"; return; } b.textContent = msg; b.className = "banner " + (kind || "info"); }
function fillSel(sel, items, value) {
  sel.innerHTML = "";
  for (const it of items) { const o = document.createElement("option"); o.value = it.value; o.textContent = it.label; sel.appendChild(o); }
  if (value != null && [...sel.options].some((o) => o.value === value)) sel.value = value;
}
function syncSpeed() { $("speedVal").textContent = (+$("speed").value).toFixed(2) + "×"; }
function syncVariants() { $("variantsVal").textContent = $("nvariants").value; }

async function boot() {
  try { INFO = await api("/api/info"); } catch (e) { banner("Не удаётся подключиться: " + e.message, "err"); return; }

  $("pillOffline").textContent = INFO.offline_mode ? "офлайн" : "онлайн";
  $("pillOffline").className = "pill" + (INFO.offline_mode ? "" : " err");
  $("hw").textContent = INFO.hardware_summary || "";
  $("foot").textContent = `LocalTTS Studio · ${INFO.ffmpeg ? "FFmpeg готов" : "⚠ нет FFmpeg"} · файлы в projects/ и output/`;

  fillSel($("bitrate"), (INFO.mp3_bitrate_choices || ["160k"]).map((b) => ({ value: b, label: b })), INFO.mp3_bitrate);

  const presets = INFO.presets || {};
  fillSel($("preset"), Object.entries(presets).map(([k, v]) => ({ value: k, label: v.label || k })),
    (INFO.settings && INFO.settings.preset) || INFO.defaults.preset);
  updatePresetHint();

  if (INFO.loading || !INFO.ready) {
    banner("Модель загружается… это займёт около минуты.", "info");
    setTimeout(boot, 2500);
  } else if (INFO.error) {
    banner("Проблема с движком: " + INFO.error, "err");
  } else banner("");

  if (INFO.ready) {
    const usable = (INFO.voices || []).filter((v) => v.usable_now && v.has_reference);
    fillSel($("voice"), usable.map((v) => ({ value: v.id, label: v.label + (v.has_transcript ? "" : "  ⚠ нет расшифровки") })),
      (INFO.settings && INFO.settings.voice) || INFO.defaults.voice);
    if (!usable.length) banner("Нет голосов с референсом. Нажмите ＋ рядом с «Голос» и добавьте запись.", "warn");
  }

  if (INFO.settings) {
    if (INFO.settings.speed) $("speed").value = INFO.settings.speed;
    if (INFO.settings.mp3_bitrate) $("bitrate").value = INFO.settings.mp3_bitrate;
    if (typeof INFO.settings.post_enabled === "boolean") $("post").checked = INFO.settings.post_enabled;
  }
  const nv = (INFO.settings && INFO.settings.n_variants) || (INFO.defaults && INFO.defaults.variants) || 1;
  $("nvariants").value = Math.max(1, Math.min(5, nv));
  syncSpeed(); syncVariants();
  if (INFO.script != null && !Editor.getValue()) Editor.setValue(INFO.script, true);
  refreshMetrics();
  renderHistory(INFO.history || []);
}

function updatePresetHint() {
  const p = (INFO && INFO.presets && INFO.presets[$("preset").value]) || {};
  $("presetHint").textContent = p.hint || "";
}

function settings() {
  return {
    voice: $("voice").value, preset: $("preset").value, speed: +$("speed").value,
    n_variants: +$("nvariants").value,
    mp3_bitrate: $("bitrate").value, post_enabled: $("post").checked,
  };
}

const save = debounce(async () => {
  try { await api("/api/workspace", { method: "PUT", body: { script: Editor.getValue(), settings: settings() } }); } catch {}
}, 1200);

const refreshMetrics = debounce(async () => {
  try {
    const d = await api("/api/analyze", { method: "POST", body: { text: Editor.getValue(), preset: $("preset").value } });
    const m = d.metrics;
    $("metrics").textContent = `${m.words} слов · ~${m.estimated_clock}` + (m.pause_seconds ? ` (паузы ${m.pause_seconds}с)` : "");
    window._lastAnalysis = d.analysis;
  } catch {}
}, 450);

function onEdit() { refreshMetrics(); save(); }

// ---- analyzer ----
async function showAnalysis() {
  let a = window._lastAnalysis;
  if (!a) { try { a = (await api("/api/analyze", { method: "POST", body: { text: Editor.getValue() } })).analysis; } catch (e) { banner(e.message, "err"); return; } }
  $("analysisBox").classList.remove("hidden");
  const issues = a.issues || [];
  if (!issues.length) { $("analysisList").innerHTML = "<div class='dim sm'>Проблем не найдено — можно озвучивать.</div>"; return; }
  $("analysisList").innerHTML = issues.map((i) =>
    `<div class="issue ${["latin", "no-vowel", "long-sentence"].includes(i.type) ? "warn" : ""}">
       <span class="k">${esc(i.type)}</span>${esc(i.msg)}</div>`).join("");
}
async function applyFixes() {
  try {
    const d = await api("/api/apply-suggestions", { method: "POST", body: { text: Editor.getValue() } });
    Editor.setValue(d.text);
    banner("Автоисправления применены. Проверьте текст.", "ok");
    setTimeout(() => banner(""), 3000);
    showAnalysis();
  } catch (e) { banner(e.message, "err"); }
}

// ---- generate (background job + polling) ----
let CUR_GID = null, SELECTED_V = 1;

async function generate() {
  const btn = $("generateBtn");
  btn.disabled = true; btn.textContent = "Генерация…"; banner("");
  $("variants").innerHTML = ""; $("resultWarn").innerHTML = "";
  $("resultBox").classList.remove("hidden");
  $("genProgress").textContent = "";
  try {
    const j = await api("/api/generate", { method: "POST", body: { script: Editor.getValue(), ...settings() } });
    await pollJob(j.job_id, j.total);
  } catch (e) { banner("Не удалось озвучить: " + e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "Озвучить"; }
}

async function pollJob(jobId, total) {
  while (true) {
    let s;
    try { s = await api("/api/generate/status/" + jobId); }
    catch (e) { banner(e.message, "err"); return; }
    if (total > 1 && s.state === "running") $("genProgress").textContent = `· генерация ${s.done} / ${s.total}`;
    if (s.state === "error") { banner("Ошибка генерации: " + s.error, "err"); $("genProgress").textContent = ""; return; }
    if (s.state === "done") { $("genProgress").textContent = ""; renderResult(s); return; }
    await new Promise((r) => setTimeout(r, 1400));
  }
}

function renderResult(d) {
  CUR_GID = d.generation_id; SELECTED_V = 1;
  $("resultMeta").textContent = `${d.preset} · ${d.realtime_factor}× realtime`;
  $("resultWarn").innerHTML = (d.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join("");
  const vs = d.variants || [];
  const multi = vs.length > 1;
  const okCount = vs.filter((v) => !v.error).length;
  if (multi) $("resultMeta").textContent += ` · ${okCount} из ${vs.length} готово`;
  $("variants").innerHTML = vs.map((v) => {
    if (v.error) return `<div class="variant bad"><div class="v-head"><strong>Вариант ${v.index}</strong>
        <span class="dim sm">✗ ошибка</span></div><div class="dim sm">${esc(v.error)}</div></div>`;
    return `<div class="variant" data-vi="${v.index}">
      <div class="v-head">
        <strong>${multi ? "Вариант " + v.index : "Результат"}</strong>
        <span class="dim sm">${v.duration_clock}${v.lufs != null ? " · " + v.lufs + " LUFS" : ""}</span>
      </div>
      <audio controls preload="none" src="${v.audio_url}"></audio>
      <div class="v-acts">
        <a class="btn primary sm" href="${v.download_url}" download>Скачать MP3</a>
        <a class="btn ghost sm" href="${v.wav_url}" download>WAV</a>
        <button class="btn ghost sm pick" data-vi="${v.index}">${multi ? "Выбрать этот" : "★ Сохранить"}</button>
      </div>
    </div>`;
  }).join("");
  if (multi) markSelected(1);
  renderHistory(d.history || []);
  $("resultBox").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function markSelected(vi) {
  SELECTED_V = vi;
  document.querySelectorAll("#variants .variant").forEach((el) => {
    el.classList.toggle("selected", +el.dataset.vi === vi);
  });
}

async function pickVariant(vi) {
  if (!CUR_GID) return;
  markSelected(vi);
  try {
    await api(`/api/keep/${CUR_GID}/${vi}`, { method: "POST" });
    banner(`Вариант ${vi} выбран и сохранён в projects/workspace/final/`, "ok");
    setTimeout(() => banner(""), 3000);
  } catch (e) { banner(e.message, "err"); }
}

function renderHistory(gens) {
  $("histCount").textContent = gens.length ? `(${gens.length})` : "";
  $("historyList").innerHTML = gens.map((g) => {
    const vi = g.selected || 1;
    const file = g.mp3 ? "final.mp3" : "final.wav";
    return `
    <div class="hist">
      <div class="r1"><span>${esc(g.voice || "")}</span><span class="dim">${g.duration_clock || ""}</span></div>
      <div class="r2">${esc(g.preset || "")} · ${g.id}${g.n_variants > 1 ? ` · вар. ${vi}/${g.n_variants}` : ""}${g.lufs != null ? " · " + g.lufs + " LUFS" : ""}</div>
      <div class="acts">
        <button data-play="/api/gen/${g.id}/variant_${vi}/${file}">▶ прослушать</button>
        <button data-keep="${g.id}" data-vi="${vi}">★ оставить</button>
      </div>
    </div>`;
  }).join("") || "<div class='dim sm'>Пока пусто.</div>";
}

// ---- voices ----
async function refreshVoices(select) {
  let d;
  try { d = await api("/api/voices"); } catch (e) { $("vStatus").textContent = e.message; return; }
  const voices = d.voices || [];
  $("voiceRows").innerHTML = voices.map((v) => `
    <div class="vrow">
      <span class="nm">${esc(v.label)} <span class="dim">(${v.gender})</span></span>
      <span class="tag ${v.has_transcript ? "ok" : "warn"}">${v.has_transcript ? "текст ✓" : "нет текста"}</span>
      <button data-editvoice="${esc(v.id)}">⚙</button>
      <button data-delvoice="${esc(v.id)}">удалить</button>
    </div>`).join("") || "<div class='dim sm'>Голосов пока нет.</div>";
  const usable = voices.filter((v) => v.usable_now && v.has_reference);
  fillSel($("voice"), usable.map((v) => ({ value: v.id, label: v.label + (v.has_transcript ? "" : "  ⚠ нет расшифровки") })),
    select || $("voice").value);
  save();
}

async function submitVoice(ev) {
  ev.preventDefault();
  const files = [...$("vFile").files];
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("audio", f);
  fd.append("id", $("vId").value || files[0].name.replace(/\.[^.]+$/, ""));
  fd.append("label", $("vLabel").value || $("vId").value);
  fd.append("gender", $("vGender").value);
  fd.append("languages", $("vLangs").value || "ru");
  fd.append("reference_text", $("vText").value);
  $("vSubmit").disabled = true;
  $("vStatus").textContent = files.length > 1 ? `склеиваю ${files.length} клипов…` : "обрабатываю…";
  try {
    const r = await fetch("/api/voices", { method: "POST", body: fd });
    const t = await r.text(); let d = {};
    try { d = JSON.parse(t); } catch { throw new Error(r.status === 413 ? "Файлы слишком большие." : `HTTP ${r.status} — перезапустите студию.`); }
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    $("vStatus").textContent = `добавлено: ${d.duration}с` + ((d.warnings || []).length ? " — " + d.warnings.join(" ") : " ✓");
    $("voiceForm").reset(); $("vFileList").innerHTML = "";
    await refreshVoices(fd.get("id"));
  } catch (e) { $("vStatus").textContent = "✗ " + e.message; }
  finally { $("vSubmit").disabled = false; }
}

function openEdit(v) {
  $("eId").value = v.id; $("eLabel").value = v.label || ""; $("eGender").value = v.gender || "unknown";
  $("eLangs").value = (v.languages || []).join(", "); $("eText").value = v.reference_text || "";
  $("eStatus").textContent = "";
  $("editVoiceModal").showModal();
}
async function submitEdit(ev) {
  ev.preventDefault();
  try {
    await api("/api/voices/" + encodeURIComponent($("eId").value), { method: "PUT", body: {
      label: $("eLabel").value, gender: $("eGender").value,
      languages: $("eLangs").value.split(",").map((s) => s.trim()).filter(Boolean),
      reference_text: $("eText").value,
    }});
    $("editVoiceModal").close();
    await refreshVoices();
    banner("Голос обновлён.", "ok"); setTimeout(() => banner(""), 2500);
  } catch (e) { $("eStatus").textContent = "✗ " + e.message; }
}

// ---- events ----
document.addEventListener("click", async (e) => {
  const pick = e.target.closest("#variants .pick");
  if (pick) { pickVariant(+pick.dataset.vi); return; }

  const play = e.target.closest("[data-play]");
  if (play) {
    const row = play.closest(".hist");
    let a = row.querySelector("audio");
    if (!a) { a = document.createElement("audio"); a.controls = true; row.appendChild(a); }
    a.src = play.dataset.play + "?t=" + Date.now(); a.play();
  }
  const keep = e.target.closest("[data-keep]");
  if (keep) {
    const gid = keep.dataset.keep, vi = keep.dataset.vi || 1;
    try { await api(`/api/keep/${gid}/${vi}`, { method: "POST" }); banner("Сохранено в projects/workspace/final/", "ok"); setTimeout(() => banner(""), 2500); }
    catch (err) { banner(err.message, "err"); }
  }
  const del = e.target.closest("[data-delvoice]");
  if (del) {
    if (!confirm(`Удалить голос «${del.dataset.delvoice}»?`)) return;
    try { await api("/api/voices/" + encodeURIComponent(del.dataset.delvoice), { method: "DELETE" }); refreshVoices(); }
    catch (err) { $("vStatus").textContent = err.message; }
  }
  const ed = e.target.closest("[data-editvoice]");
  if (ed) {
    const d = await api("/api/voices");
    const v = (d.voices || []).find((x) => x.id === ed.dataset.editvoice);
    if (v) openEdit(v);
  }
});

$("addVoiceBtn").addEventListener("click", () => { $("voicesModal").showModal(); refreshVoices(); });
$("voiceForm").addEventListener("submit", submitVoice);
$("editForm").addEventListener("submit", submitEdit);
$("vFile").addEventListener("change", () => {
  const fs = [...$("vFile").files];
  if (!$("vId").value && fs[0]) $("vId").value = fs[0].name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_\- ]+/g, "_").slice(0, 40);
  $("vFileList").innerHTML = fs.length < 2 ? "" :
    `<div class="dim sm">склеятся в этом порядке:</div>` + fs.map((f, i) => `<div class="f">${i + 1}. ${esc(f.name)}</div>`).join("");
});
$("preset").addEventListener("change", () => { updatePresetHint(); refreshMetrics(); save(); });
$("voice").addEventListener("change", save);
$("speed").addEventListener("input", () => { syncSpeed(); save(); });
$("nvariants").addEventListener("input", () => { syncVariants(); save(); });
$("bitrate").addEventListener("change", save);
$("post").addEventListener("change", save);
$("analyzeBtn").addEventListener("click", showAnalysis);
$("applyFixes").addEventListener("click", applyFixes);
$("closeAnalysis").addEventListener("click", () => $("analysisBox").classList.add("hidden"));
$("generateBtn").addEventListener("click", generate);
$("undoBtn").addEventListener("click", () => Editor.undo());
$("redoBtn").addEventListener("click", () => Editor.redo());

Editor.init({ textarea: $("script"), onChange: onEdit });
syncSpeed(); syncVariants();
boot();

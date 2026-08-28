"use strict";

const $ = (s) => document.querySelector(s);
const el = (t, cls, txt) => {
  const n = document.createElement(t);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};

let SCRIPT = null;
let LINE_OFFSET = 0;
let CLIP_OFFSET = 0;

async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.status + " " + res.statusText);
  return data;
}

let toastTimer = null;
function toast(msg, kind) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind ? " " + kind : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4200);
}

function stat(k, v, kind) {
  const d = el("div", "stat" + (kind ? " " + kind : ""));
  d.appendChild(el("div", "k", k));
  d.appendChild(el("div", "v", v));
  return d;
}

const num = (n) => (n === null || n === undefined ? "-" : n.toLocaleString());

/* ---------------- tabs ---------------- */

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
  });
});

/* ---------------- boot ---------------- */

async function boot() {
  try {
    const d = await api("/api/scripts");
    const sel = $("#scriptSel");
    sel.innerHTML = "";
    d.scripts.forEach((s) => {
      const o = el("option", null, s.voice);
      o.value = s.script;
      sel.appendChild(o);
    });
    SCRIPT = d.scripts.length ? d.scripts[0].script : null;
    renderScriptCards(d.scripts);
  } catch (e) {
    toast("스크립트 목록을 읽지 못했습니다: " + e.message, "bad");
  }

  try {
    const s = await api("/api/subscription");
    $("#credit").innerHTML = s.error
      ? "<span style='color:var(--bad)'>크레딧 조회 실패</span>"
      : `${s.tier} · 잔여 <b>${num(s.remaining)}</b> / ${num(s.limit)}자`;
  } catch (e) {
    $("#credit").textContent = "크레딧 조회 실패";
  }

  subscribeJob();
}

$("#scriptSel").addEventListener("change", (e) => {
  SCRIPT = e.target.value;
  $("#previewSummary").innerHTML = "";
  $("#rewriteBox").classList.add("hidden");
  $("#lineRows").innerHTML = "";
  $("#clipList").innerHTML = "";
  $("#clipStats").textContent = "";
});

function renderScriptCards(scripts) {
  const wrap = $("#scriptCards");
  wrap.innerHTML = "";
  if (!scripts.length) {
    wrap.appendChild(el("p", "hint", "프로젝트 폴더에 .txt 스크립트가 없습니다."));
    return;
  }
  scripts.forEach((s) => {
    const c = el("div", "card");
    c.appendChild(el("h4", null, s.voice));
    c.appendChild(el("div", "sub", s.script));
    if (s.error) {
      const e = el("div", "warn", s.error);
      c.appendChild(e);
    } else {
      const pct = s.lines ? Math.round((s.generated / s.lines) * 100) : 0;
      const p = el("div", "prog");
      p.appendChild(el("div", "sub", `${num(s.generated)} / ${num(s.lines)} 클립 · ${pct}%`));
      const bar = el("div", "bar");
      const fill = el("div", "bar-fill");
      fill.style.width = pct + "%";
      bar.appendChild(fill);
      p.appendChild(bar);
      c.appendChild(p);
    }
    wrap.appendChild(c);
  });
}

/* ---------------- job stream ---------------- */

function subscribeJob() {
  const src = new EventSource("/api/job/stream");
  src.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    renderJob(d.job);
  };
  src.onerror = () => {
    // EventSource reconnects on its own; nothing to do.
  };
}

let lastJobState = null;

function renderJob(job) {
  const bar = $("#jobbar");
  if (!job) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  $("#jobLabel").textContent = job.label;

  const p = job.progress;
  const pct = p && p.total ? Math.round((p.done / p.total) * 100) : (job.state === "done" ? 100 : 0);
  $("#jobFill").style.width = pct + "%";

  let s = job.state;
  if (p) {
    const eta = p.eta ? `eta ${Math.floor(p.eta / 60)}m${String(Math.round(p.eta % 60)).padStart(2, "0")}s` : "";
    s = `${p.done}/${p.total} · 건너뜀 ${p.skipped} · 실패 ${p.failed} · 플래그 ${p.flagged} · ${p.rate}/s ${eta}`;
  } else if (job.state === "running") {
    s = "준비 중…";
  }
  if (job.state === "failed") s = "실패: " + job.error;
  if (job.state === "cancelled") s = "중단됨";
  if (job.state === "done" && job.result && job.result.items)
    s = `${job.result.items.length}건 (${job.result.mode === "check" ? "검사" : "적용"})`;
  $("#jobStat").textContent = s;

  $("#jobStop").classList.toggle("hidden", job.state !== "running");
  $("#jobLog").innerHTML = "";
  (job.log || []).slice(-6).forEach((l) => {
    $("#jobLog").appendChild(el("div", null, `[${l.t}s] ${l.line}`));
  });

  if (lastJobState === "running" && job.state !== "running") {
    if (job.state === "done") {
      toast("완료: " + job.label, "good");
      if (job.result && job.result.items !== undefined) renderToolResult(job);
    } else if (job.state === "failed") toast("실패: " + job.error, "bad");
    else toast("중단됨: " + job.label);
  }
  lastJobState = job.state;
}

$("#jobStop").addEventListener("click", async () => {
  await api("/api/job/stop", {});
  toast("중단을 요청했습니다. 진행 중인 클립까지 마치고 멈춥니다.");
});

/* ---------------- preview ---------------- */

$("#btnPreview").addEventListener("click", async () => {
  if (!SCRIPT) return;
  const btn = $("#btnPreview");
  btn.disabled = true;
  btn.textContent = "처리 중…";
  try {
    const r = await api("/api/preview", { script: SCRIPT });
    const box = $("#previewSummary");
    box.innerHTML = "";
    box.appendChild(stat("줄", num(r.entries)));
    box.appendChild(stat("변경된 줄", num(r.changed)));
    box.appendChild(stat("숫자 인접 조사", num(r.numericFixes)));
    box.appendChild(stat("단어 재작성", num(r.wordRewrites.reduce((a, x) => a + x.count, 0)),
      r.wordRewrites.length ? "warn" : "good"));
    box.appendChild(stat("발화 문자", num(r.bodyChars)));
    box.appendChild(stat("과금 문자", num(r.billedChars)));
    renderRewrites(r);
  } catch (e) {
    toast(e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "정규화 실행 (API 호출 없음)";
  }
});

function renderRewrites(r) {
  const box = $("#rewriteBox");
  const tbl = $("#rewriteTbl");
  tbl.innerHTML = "";
  if (!r.wordRewrites.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");

  const head = el("tr");
  ["변경 전", "변경 후", "건수", ""].forEach((h) => head.appendChild(el("th", null, h)));
  tbl.appendChild(head);

  r.wordRewrites.forEach((w) => {
    const tr = el("tr");
    tr.appendChild(el("td", null, w.from));
    tr.appendChild(el("td", null, w.to));
    tr.appendChild(el("td", null, String(w.count)));
    const td = el("td");
    const isProtected = r.protected.indexOf(w.from) >= 0;
    const b = el("button", "btn btn-sm", isProtected ? "보호됨" : "보호 목록에 추가");
    b.disabled = isProtected;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api("/api/protect", { token: w.from });
        toast(`${w.from} 보호 완료 — 서버를 재시작해야 반영됩니다.`, "good");
        b.textContent = "재시작 필요";
      } catch (e) { toast(e.message, "bad"); b.disabled = false; }
    });
    td.appendChild(b);
    tr.appendChild(td);
    tbl.appendChild(tr);
  });
}

/* word-level diff so a particle fix is visible at a glance */
function diffWords(a, b) {
  const A = a.split(" "), B = b.split(" ");
  const out = document.createDocumentFragment();
  if (A.length !== B.length) {
    out.appendChild(el("del", null, a));
    out.appendChild(document.createTextNode(" → "));
    out.appendChild(el("ins", null, b));
    return out;
  }
  B.forEach((w, i) => {
    if (i) out.appendChild(document.createTextNode(" "));
    if (w === A[i]) out.appendChild(document.createTextNode(w));
    else {
      out.appendChild(el("del", null, A[i]));
      out.appendChild(document.createTextNode(" "));
      out.appendChild(el("ins", null, w));
    }
  });
  return out;
}

async function loadLines(append) {
  if (!SCRIPT) return;
  if (!append) { LINE_OFFSET = 0; $("#lineRows").innerHTML = ""; }
  try {
    const r = await api("/api/lines", {
      script: SCRIPT, q: $("#lineSearch").value,
      changed: $("#onlyChanged").checked, offset: LINE_OFFSET, limit: 100,
    });
    $("#lineCount").textContent = `${num(r.total)}줄`;
    const wrap = $("#lineRows");
    r.rows.forEach((row) => {
      const d = el("div", "linerow");
      const h = el("div");
      h.appendChild(el("span", "id", row.wav + "  "));
      if (row.emotion !== "neutral") h.appendChild(el("span", "pill emo", row.emotion));
      d.appendChild(h);
      if (row.changed) {
        const s = el("div", "say");
        s.appendChild(diffWords(row.raw, row.say));
        d.appendChild(s);
      } else {
        d.appendChild(el("div", "say", row.say));
      }
      wrap.appendChild(d);
    });
    LINE_OFFSET += r.rows.length;
    $("#btnMoreLines").classList.toggle("hidden", LINE_OFFSET >= r.total);
  } catch (e) { toast(e.message, "bad"); }
}

$("#btnLines").addEventListener("click", () => loadLines(false));
$("#btnMoreLines").addEventListener("click", () => loadLines(true));
$("#lineSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") loadLines(false); });

/* ---------------- generate ---------------- */

$("#btnEstimate").addEventListener("click", async () => {
  if (!SCRIPT) return;
  try {
    const limit = $("#genLimit").value ? parseInt($("#genLimit").value, 10) : null;
    const r = await api("/api/estimate", { script: SCRIPT, limit });
    const box = $("#estimateBox");
    box.innerHTML = "";
    box.appendChild(stat("필요", num(r.need) + "자"));
    if (r.remaining !== undefined) {
      box.appendChild(stat("잔여", num(r.remaining) + "자"));
      box.appendChild(stat("실행 후", num(r.after) + "자", r.enough ? "good" : "bad"));
      if (!r.enough) toast("크레딧이 부족합니다.", "bad");
    }
  } catch (e) { toast(e.message, "bad"); }
});

$("#btnGenerate").addEventListener("click", async () => {
  if (!SCRIPT) return;
  const limit = $("#genLimit").value ? parseInt($("#genLimit").value, 10) : null;
  let est;
  try {
    est = await api("/api/estimate", { script: SCRIPT, limit });
  } catch (e) { toast(e.message, "bad"); return; }

  const msg = `${num(est.need)}자를 사용합니다.` +
    (est.remaining !== undefined ? ` 잔여 ${num(est.remaining)} → ${num(est.after)}자.` : "") +
    "\n\n크레딧이 실제로 차감됩니다. 진행할까요?";
  if (!confirm(msg)) return;
  if (est.enough === false && !confirm("크레딧이 부족합니다. 그래도 시작할까요?")) return;

  try {
    await api("/api/generate", {
      script: SCRIPT, limit,
      trim: parseInt($("#genTrim").value, 10),
      concurrency: parseInt($("#genConc").value, 10),
      force: $("#genForce").checked,
    });
    toast("생성을 시작했습니다.", "good");
  } catch (e) { toast(e.message, "bad"); }
});

$("#btnRetry").addEventListener("click", async () => {
  if (!SCRIPT) return;
  if (!confirm("실패·플래그된 클립을 다시 생성합니다. 크레딧이 차감됩니다. 진행할까요?")) return;
  try {
    await api("/api/retry", {
      script: SCRIPT,
      includeFlagged: $("#retryFlagged").checked,
      take: parseInt($("#retryTake").value, 10),
    });
    toast("재생성을 시작했습니다.", "good");
  } catch (e) { toast(e.message, "bad"); }
});

/* ---------------- tools ---------------- */

document.querySelectorAll("[data-tool]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    if (!SCRIPT) return;
    const tool = btn.dataset.tool;
    const check = btn.dataset.check === "1";
    const pad = parseInt($(tool === "retrim" ? "#retrimPad" : "#declickPad").value, 10);
    if (!check && tool === "declick" &&
        !confirm("클립 오디오를 잘라냅니다. 원본은 originals/ 에 백업되고 Review 탭에서 되돌릴 수 있습니다. 진행할까요?"))
      return;
    try {
      await api("/api/tool", { script: SCRIPT, tool, pad, check });
    } catch (e) { toast(e.message, "bad"); }
  });
});

function renderToolResult(job) {
  const box = $("#toolResult");
  const body = $("#toolBody");
  box.classList.remove("hidden");
  body.innerHTML = "";
  const items = job.result.items || [];
  body.appendChild(el("p", "hint",
    `${job.kind} · ${job.result.mode === "check" ? "검사" : "적용"} · ${items.length}건`));
  if (!items.length) return;
  const tbl = el("table", "tbl");
  const head = el("tr");
  Object.keys(items[0]).forEach((k) => head.appendChild(el("th", null, k)));
  tbl.appendChild(head);
  items.slice(0, 200).forEach((it) => {
    const tr = el("tr");
    Object.values(it).forEach((v) => tr.appendChild(el("td", null, String(v))));
    tbl.appendChild(tr);
  });
  body.appendChild(tbl);
}

/* ---------------- review ---------------- */

async function loadClips(append) {
  if (!SCRIPT) return;
  if (!append) { CLIP_OFFSET = 0; $("#clipList").innerHTML = ""; }
  try {
    const r = await api("/api/clips", {
      script: SCRIPT, filter: $("#clipFilter").value, q: $("#clipSearch").value,
      offset: CLIP_OFFSET, limit: 60,
    });
    const flags = Object.entries(r.flagCounts || {})
      .map(([k, v]) => `${k} ${v}`).join(" · ");
    $("#clipStats").textContent =
      `${num(r.total)}건 표시` + (flags ? ` · ${flags}` : " · QC 플래그 없음") +
      (r.restorable ? ` · 되돌릴 수 있는 클립 ${r.restorable}` : "");
    r.rows.forEach((c) => $("#clipList").appendChild(clipCard(c)));
    CLIP_OFFSET += r.rows.length;
    $("#btnMoreClips").classList.toggle("hidden", CLIP_OFFSET >= r.total);
  } catch (e) { toast(e.message, "bad"); }
}

function clipCard(c) {
  const d = el("div", "clip" + (c.flags.length ? " flagged" : ""));
  const head = el("div", "clip-head");
  head.appendChild(el("span", "clip-id", c.wav.replace(".wav", "")));
  if (c.emotion && c.emotion !== "neutral")
    head.appendChild(el("span", "pill emo", c.emotion));
  head.appendChild(el("span", "clip-text", c.text));
  c.flags.forEach((f) => head.appendChild(el("span", "pill flag", f)));
  head.appendChild(el("span", "clip-meta",
    `${c.duration ?? "?"}s  lead ${c.leadSilence ?? "?"}  tail ${c.tailSilence ?? "?"}`));

  const play = el("button", "btn btn-sm", "재생");
  const audio = new Audio(`/api/audio?script=${encodeURIComponent(SCRIPT)}&wav=${c.wav}`);
  play.addEventListener("click", () => {
    if (audio.paused) { audio.play(); play.textContent = "정지"; }
    else { audio.pause(); audio.currentTime = 0; play.textContent = "재생"; }
  });
  audio.addEventListener("ended", () => { play.textContent = "재생"; });
  head.appendChild(play);

  const detail = el("button", "btn btn-sm", "파형");
  head.appendChild(detail);
  d.appendChild(head);

  const waveBox = el("div");
  d.appendChild(waveBox);

  let loaded = false;
  detail.addEventListener("click", async () => {
    if (loaded) { waveBox.innerHTML = ""; loaded = false; return; }
    loaded = true;
    waveBox.innerHTML = "";
    const pair = el("div", c.restorable ? "wave-pair" : "");
    pair.appendChild(await waveBlock(c.wav, "current",
      c.restorable ? "현재 (잘라냄)" : "현재"));
    if (c.restorable) pair.appendChild(await waveBlock(c.wav, "original", "원본"));
    waveBox.appendChild(pair);

    if (c.restorable) {
      const row = el("div", "row");
      const orig = el("button", "btn btn-sm", "원본 재생");
      const oa = new Audio(`/api/audio?script=${encodeURIComponent(SCRIPT)}&wav=${c.wav}&which=original`);
      orig.addEventListener("click", () => {
        if (oa.paused) { oa.play(); orig.textContent = "정지"; }
        else { oa.pause(); oa.currentTime = 0; orig.textContent = "원본 재생"; }
      });
      oa.addEventListener("ended", () => { orig.textContent = "원본 재생"; });
      row.appendChild(orig);

      const rev = el("button", "btn btn-sm btn-danger", "원본으로 되돌리기");
      rev.addEventListener("click", async () => {
        if (!confirm(c.wav + " 을 잘라내기 전 원본으로 되돌립니다.")) return;
        try {
          await api("/api/restore", { script: SCRIPT, wavs: [c.wav] });
          toast(c.wav + " 복원 완료", "good");
          loadClips(false);
        } catch (e) { toast(e.message, "bad"); }
      });
      row.appendChild(rev);
      waveBox.appendChild(row);
    }
  });

  return d;
}

async function waveBlock(wav, which, label) {
  const box = el("div");
  box.appendChild(el("div", "lbl", label));
  const canvas = el("canvas", "wave");
  box.appendChild(canvas);
  try {
    const r = await api(
      `/api/waveform?script=${encodeURIComponent(SCRIPT)}&wav=${wav}&which=${which}`);
    box.querySelector(".lbl").textContent =
      `${label} · ${r.duration}s · tail ${r.tailSilence}s` +
      (r.flags.length ? ` · ${r.flags.join(",")}` : "");
    drawWave(canvas, r.peaks, r.flags.length > 0);
  } catch (e) {
    box.appendChild(el("div", "hint", "파형을 불러오지 못했습니다"));
  }
  return box;
}

function drawWave(canvas, peaks, flagged) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 600, h = 46;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = flagged ? "#e0a340" : "#5b9dff";
  const n = peaks.length || 1;
  const bw = w / n;
  peaks.forEach((p, i) => {
    const ph = Math.max(1, p * (h - 4));
    ctx.fillRect(i * bw, (h - ph) / 2, Math.max(1, bw - 0.4), ph);
  });
}

$("#btnClips").addEventListener("click", () => loadClips(false));
$("#btnMoreClips").addEventListener("click", () => loadClips(true));
$("#clipFilter").addEventListener("change", () => loadClips(false));
$("#clipSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") loadClips(false); });

boot();

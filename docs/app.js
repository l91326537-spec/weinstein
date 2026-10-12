/* 스테이지 — 와인스테인 주봉 앱 (데이터: data/summary.json, data/t/<티커>.json) */
"use strict";

/* ---------- 공통 ---------- */
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => (v === null || v === undefined || v === "" || isNaN(+v)) ? null : +v;
const fmt = (v, d = 2) => { const n = num(v); if (n === null) return "–"; return n >= 1000 ? n.toLocaleString("en-US", { maximumFractionDigits: d }) : (+n.toFixed(d)).toString(); };
const pct = (v, d = 1) => { const n = num(v); return n === null ? "–" : (n > 0 ? "+" : "") + n.toFixed(d) + "%"; };
const sgn = (v, d = 1) => { const n = num(v); return n === null ? "–" : `<span class="${n > 0 ? "pos" : n < 0 ? "neg" : ""}">${pct(n, d)}</span>`; };
const stageKey = n => [1, 2, 3, 4].includes(+n) ? +n : 0;
const pill = (n, label) => { const k = stageKey(n); return `<span class="pill st${k}">${esc(label || (k ? k + "단계" : "미확인"))}</span>`; };
const band = s => s == null ? "var(--s0)" : s >= 80 ? "var(--s2)" : s >= 65 ? "#5f8a2c" : s >= 50 ? "var(--s3)" : "var(--s4)";
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const uid = () => Math.random().toString(36).slice(2, 10);
function go(h) { if (location.hash === h) route(); else location.hash = h; }
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 2200); }

function ring(score, size = 44) {
  const s = num(score), r = 40, C = 2 * Math.PI * r, arc = C * .75, val = s == null ? 0 : Math.max(0, Math.min(100, s)) / 100 * arc;
  return `<svg width="${size}" height="${size}" viewBox="0 0 100 100" role="img" aria-label="점수 ${s ?? "없음"}">
    <circle cx="50" cy="50" r="${r}" fill="none" stroke="var(--sunk)" stroke-width="11" stroke-linecap="round" stroke-dasharray="${arc} ${C}" transform="rotate(135 50 50)"/>
    <circle cx="50" cy="50" r="${r}" fill="none" stroke="${band(s)}" stroke-width="11" stroke-linecap="round" stroke-dasharray="${val} ${C}" transform="rotate(135 50 50)"/>
    <text x="50" y="${s == null ? 57 : 61}" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-weight="800" font-size="${s == null ? 22 : 34}" fill="var(--fg)">${s == null ? "–" : s}</text></svg>`;
}
function grade9(label, g) {
  const lit = g == null ? 0 : 10 - g, c = g == null ? "var(--s0)" : g <= 3 ? "var(--s2)" : g <= 6 ? "var(--s3)" : "var(--s4)";
  return `<div class="brow"><span>${label}</span><div class="seg9">${Array.from({ length: 9 }, (_, i) => `<i style="${i < lit ? "background:" + c : ""}"></i>`).join("")}</div><span class="v"><b style="color:${c}">${g ?? "–"}</b>등급</span></div>`;
}

/* ---------- 저장 (이 기기에만) ---------- */
const STORE_KEY = "stage_app_v1";
const store = (() => {
  let d = { settings: { account: 10000000, riskPct: 1, maxPos: 6 }, holdings: [], trades: [] };
  try { const raw = localStorage.getItem(STORE_KEY); if (raw) d = Object.assign(d, JSON.parse(raw)); } catch (e) { }
  return {
    get: () => d,
    save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(d)); return true; } catch (e) { toast("저장하지 못했습니다. 백업을 받아 두세요."); return false; } },
    replace(n) { d = Object.assign({ settings: {}, holdings: [], trades: [] }, n); this.save(); }
  };
})();

/* ---------- 데이터 ---------- */
let SUM = null;
const tcache = {};
async function loadSummary() {
  const r = await fetch("data/summary.json?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error("summary");
  SUM = await r.json();
  const u = new Date(SUM.updated);
  $("#updated").innerHTML = `${u.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} 갱신<br>${SUM.weekComplete ? "이번 주봉 확정" : "주중 · 주봉 미확정"}`;
}
function fileOf(t) { const it = SUM?.index.find(x => x.t === t); return it ? it.f : t.replace(/[^A-Za-z0-9._-]/g, "_"); }
async function loadT(t) {
  if (tcache[t]) return tcache[t];
  const r = await fetch(`data/t/${encodeURIComponent(fileOf(t))}.json?u=${encodeURIComponent(SUM?.updated || "")}`);
  if (!r.ok) throw new Error("no data");
  return tcache[t] = await r.json();
}
const isKRW = t => /\.(KS|KQ)$/.test(t);
const fxRate = () => (SUM && SUM.fx) || 1400;
const toKRW = (v, t) => isKRW(t) ? v : v * fxRate();
const W = { d: 0, o: 1, h: 2, l: 3, c: 4, v: 5, ma: 6, rs: 7 };

/* ---------- 손절선 계산 (초기 → 본전 → 추적) ---------- */
function weekIndex(w, date) { // date가 들어 있는 주봉 인덱스
  let k = -1;
  for (let i = 0; i < w.length; i++) { if (w[i][W.d] <= date) k = i; else break; }
  return k;
}
function trailStop(w, h) {
  const buy = +h.buyPrice, s0 = +h.initStop;
  let s = s0, be = false, broke = null;
  const k = weekIndex(w, h.buyDate);
  if (k < 0) return { stop: s, phase: "초기 손절", broke, held: 0 };
  const risk = buy - s0;
  for (let j = k; j < w.length; j++) {
    if (j > k && w[j][W.l] <= s) { broke = w[j][W.d]; break; }
    const held = j - k, c = w[j][W.c];
    if (!be && (c >= buy + risk || (held >= 3 && c > buy))) { be = true; s = Math.max(s, buy * 1.002); }
    if (be) {
      let lo = Infinity; for (let q = Math.max(0, j - 7); q <= j; q++) lo = Math.min(lo, w[q][W.l]);
      s = Math.max(s, Math.max(w[j][W.ma] || 0, lo) * 0.99);
    }
  }
  const phase = !be ? "초기 손절" : s <= buy * 1.0025 ? "본전" : "추적 손절";
  return { stop: s, phase, broke, held: w.length - 1 - k, risk };
}
function holdStatus(td, h) {
  const w = td.w, last = w[w.length - 1], close = last[W.c];
  const tr = trailStop(w, h);
  const dist = (close - tr.stop) / close * 100;
  let st = { t: "정상", c: "st2" };
  if (tr.broke) st = { t: "손절 이탈", c: "st4" };
  else if (td.stage === 4) st = { t: "4단계 · 매도 검토", c: "st4" };
  else if (dist < 3) st = { t: "손절 근접", c: "st3" };
  else if (td.stage === 3) st = { t: "3단계 경고", c: "st3" };
  const ref = tr.broke ? tr.stop : close;     // 손절을 깼으면 손절가에 판 것으로 계산
  const pl = (ref / h.buyPrice - 1) * 100, R = tr.risk > 0 ? (ref - h.buyPrice) / tr.risk : null;
  return { ...tr, close, dist, st, pl, R };
}

/* ---------- 규칙 점검 (매수 기록 시) ---------- */
function ruleCheck(td) {
  const v = [];
  if (SUM && SUM.worst === 4) v.push("시장 4단계");
  if (td.score == null || td.score < 65) v.push("점수 65 미만");
  if (td.stage !== 2) v.push("2단계 아님");
  if ([3, 4].includes(td.sectorStage)) v.push("업종 " + td.sectorStage + "단계");
  if (num(td.ext) !== null && td.ext >= 20) v.push("과열(이격 20%+)");
  return v;
}

/* ---------- 차트 (SVG) ---------- */
function drawChart(box, td, weeks) {
  const w = td.w.slice(-weeks), n = w.length;
  const W_ = Math.max(300, box.clientWidth), H = 300, RH = 86, pr = 48, pt = 8;
  const plotW = W_ - pr - 2, priceH = H - 24, volH = 54;
  let lo = Infinity, hi = -Infinity, vmax = 0, rlo = 0, rhi = 0;
  for (const r of w) { lo = Math.min(lo, r[W.l], r[W.ma] ?? Infinity); hi = Math.max(hi, r[W.h], r[W.ma] ?? -Infinity); vmax = Math.max(vmax, r[W.v]); if (r[W.rs] != null) { rlo = Math.min(rlo, r[W.rs]); rhi = Math.max(rhi, r[W.rs]); } }
  const padv = (hi - lo) * .06; lo -= padv; hi += padv;
  const x = i => 2 + (i + .5) * plotW / n, bw = Math.max(1, plotW / n * .62);
  const y = v => pt + (hi - v) / (hi - lo) * (priceH - pt - volH * .35);
  const vy = v => priceH - v / (vmax || 1) * volH;
  const rr = Math.max(Math.abs(rlo), Math.abs(rhi), 5) * 1.1;
  const ry = v => H + 10 + (rr - v) / (2 * rr) * (RH - 12);
  let s = `<svg viewBox="0 0 ${W_} ${H + RH + 14}" height="${H + RH + 14}" role="img" aria-label="${esc(td.t)} 주봉 차트">`;
  // 격자 + 가격 눈금
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi - lo) * (g + .5) / 5, yy = y(v);
    if (yy < pt || yy > priceH - volH * .35) continue;
    s += `<line x1="2" x2="${plotW + 2}" y1="${yy}" y2="${yy}" stroke="var(--line)" stroke-width="1"/><text x="${W_ - 4}" y="${yy + 4}" text-anchor="end" font-size="10" fill="var(--muted)" font-family="ui-monospace,Menlo,monospace">${fmt(v, v < 10 ? 2 : 1)}</text>`;
  }
  // 연·월 눈금
  let lastY = "";
  w.forEach((r, i) => { const yy = r[W.d].slice(0, 4), m = r[W.d].slice(5, 7); const key = n > 70 ? yy : yy + m; if (key !== lastY && i > 0 && (n > 70 ? true : ["01", "04", "07", "10"].includes(m))) { s += `<line x1="${x(i)}" x2="${x(i)}" y1="${pt}" y2="${priceH}" stroke="var(--line)" stroke-dasharray="2 3"/><text x="${x(i) + 3}" y="${priceH + 14}" font-size="10" fill="var(--muted)">${n > 70 ? yy : yy.slice(2) + "." + m}</text>`; } lastY = key; });
  // 거래량
  w.forEach((r, i) => { s += `<rect x="${x(i) - bw / 2}" y="${vy(r[W.v])}" width="${bw}" height="${priceH - vy(r[W.v])}" fill="var(--vol)"/>`; });
  // 캔들
  w.forEach((r, i) => {
    const up = r[W.c] >= r[W.o], col = up ? "var(--up)" : "var(--dn)";
    const yo = y(r[W.o]), yc = y(r[W.c]);
    s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(r[W.h])}" y2="${y(r[W.l])}" stroke="${col}" stroke-width="1"/>`;
    s += `<rect x="${x(i) - bw / 2}" y="${Math.min(yo, yc)}" width="${bw}" height="${Math.max(1, Math.abs(yc - yo))}" fill="${up ? "var(--surface)" : col}" stroke="${col}" stroke-width="1"/>`;
  });
  // 30주선
  const mp = w.map((r, i) => r[W.ma] == null ? null : `${x(i).toFixed(1)},${y(r[W.ma]).toFixed(1)}`).filter(Boolean).join(" ");
  if (mp) s += `<polyline points="${mp}" fill="none" stroke="var(--ma)" stroke-width="2.2"/>`;
  // 기준선(매수 스톱·손절)
  const lines = [[td.buyStop, "var(--s2)", "매수"], [td.stop, "var(--s4)", "손절"]];
  for (const [v, c, lab] of lines) {
    if (num(v) === null || v < lo || v > hi) continue;
    s += `<line x1="2" x2="${plotW + 2}" y1="${y(v)}" y2="${y(v)}" stroke="${c}" stroke-dasharray="5 4" stroke-width="1.2"/><text x="6" y="${y(v) - 3}" font-size="10" font-weight="700" fill="${c}">${lab} ${fmt(v)}</text>`;
  }
  // 마지막 가격
  const lc = w[n - 1][W.c];
  s += `<rect x="${plotW + 4}" y="${y(lc) - 8}" width="${pr - 4}" height="16" rx="3" fill="var(--fg)"/><text x="${W_ - 4}" y="${y(lc) + 4}" text-anchor="end" font-size="10" font-weight="700" fill="var(--bg)" font-family="ui-monospace,Menlo,monospace">${fmt(lc, lc < 10 ? 2 : 1)}</text>`;
  // RS
  s += `<text x="4" y="${H + 8}" font-size="10" fill="var(--muted)">맨스필드 RS</text><line x1="2" x2="${plotW + 2}" y1="${ry(0)}" y2="${ry(0)}" stroke="var(--muted)" stroke-width="1"/>`;
  const rp = w.map((r, i) => r[W.rs] == null ? null : [x(i), ry(r[W.rs]), r[W.rs]]).filter(Boolean);
  if (rp.length) {
    const base = ry(0);
    s += `<path d="M${rp[0][0]},${base} ${rp.map(p => `L${p[0].toFixed(1)},${Math.min(p[1], base).toFixed(1)}`).join(" ")} L${rp[rp.length - 1][0]},${base}Z" fill="var(--s2)" opacity=".18"/>`;
    s += `<path d="M${rp[0][0]},${base} ${rp.map(p => `L${p[0].toFixed(1)},${Math.max(p[1], base).toFixed(1)}`).join(" ")} L${rp[rp.length - 1][0]},${base}Z" fill="var(--s4)" opacity=".18"/>`;
    s += `<polyline points="${rp.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ")}" fill="none" stroke="var(--fg)" stroke-width="1.4"/>`;
    s += `<text x="${W_ - 4}" y="${rp[rp.length - 1][1] + 4}" text-anchor="end" font-size="10" font-family="ui-monospace,Menlo,monospace" fill="var(--fg)">${fmt(rp[rp.length - 1][2], 1)}</text>`;
  }
  s += `<line id="xh" x1="0" x2="0" y1="${pt}" y2="${H + RH}" stroke="var(--fg)" stroke-width="1" opacity="0"/></svg>`;
  box.innerHTML = s;
  const info = box.parentElement.querySelector(".chartinfo");
  const show = i => {
    const r = w[i]; if (!r) return;
    $("#xh", box).setAttribute("x1", x(i)); $("#xh", box).setAttribute("x2", x(i)); $("#xh", box).setAttribute("opacity", ".35");
    const ch = i > 0 ? (r[W.c] / w[i - 1][W.c] - 1) * 100 : null;
    info.innerHTML = `<span>${r[W.d]}</span><span>종가 <b>${fmt(r[W.c])}</b> ${ch === null ? "" : sgn(ch)}</span><span>30주선 <b>${fmt(r[W.ma])}</b></span><span>RS <b>${fmt(r[W.rs], 1)}</b></span><span>거래량 <b>${(r[W.v] / 1e6).toFixed(1)}M</b></span>`;
  };
  const svg = $("svg", box);
  const pick = e => { const b = svg.getBoundingClientRect(); const px = (e.clientX - b.left) * W_ / b.width; show(Math.max(0, Math.min(n - 1, Math.floor((px - 2) / (plotW / n))))); };
  svg.addEventListener("pointermove", pick); svg.addEventListener("pointerdown", pick);
  show(n - 1);
}

/* ---------- 화면: 홈 ---------- */
async function viewHome(v) {
  const mk = SUM.market, worst = SUM.worst;
  const mrow = Object.entries(mk).filter(([, m]) => m).map(([t, m]) => `<div class="row between"><span>${esc(m.name)}</span><span class="row">${pill(m.stage, m.label)}<span class="mono muted" style="font-size:12px">30주선 ${pct(m.ext)}</span></span></div>`).join("");
  const top = SUM.candidates.filter(c => c.signal).sort((a, b) => b.score - a.score).slice(0, 5);
  const secs = SUM.sectors;
  v.innerHTML = `
  <section class="card"><h3>시장</h3>${mrow}
    ${worst === 4 ? `<div class="warn">시장 4단계: 와인스테인 원칙상 신규 매수 보류</div>` : worst === 3 ? `<div class="warn">시장 3단계: 매수 규모 축소, 손절 좁히기</div>` : ""}
  </section>
  <section class="card" id="alerts" hidden></section>
  <section class="card"><div class="row between"><h2>지금 볼 후보</h2><a class="btn sm" href="#cands">전체</a></div>
    ${top.length ? `<div class="list">${top.map((c, i) => candItem(c, i + 1)).join("")}</div>` : `<p class="note">이번 주 돌파·풀백 신호가 없습니다.</p>`}
  </section>
  <section class="card"><h2>업종 단계</h2><p class="note">업종이 종목만큼 중요합니다. 2단계 업종의 선두 종목부터 보세요.</p>
    <div class="list" id="seclist">${secs.map((s, i) => `<a class="item"${i >= 10 ? " hidden" : ""} href="#stock/${encodeURIComponent(s.etf)}">
      ${pill(s.stage)}<span style="min-width:0"><b>${esc(s.name)}</b> <span class="mono muted" style="font-size:12px">${esc(s.etf)}</span>
      <div class="n">${s.top.length ? "선두: " + s.top.map(m => `${esc(m.t)} ${m.sc}`).join(" · ") : esc(s.label)}</div></span>
      <span class="r"><span class="muted" style="font-size:11px">RS</span><b class="${(s.rs ?? 0) >= 0 ? "pos" : "neg"}">${fmt(s.rs, 1)}</b></span></a>`).join("")}</div>
    ${secs.length > 10 ? `<button class="btn sm" id="moresec">업종 ${secs.length - 10}개 더 보기</button>` : ""}
  </section>`;
  const ms = $("#moresec", v); if (ms) ms.onclick = () => { v.querySelectorAll("#seclist [hidden]").forEach(e => e.hidden = false); ms.remove(); };
  renderAlerts();
}
async function renderAlerts() {
  const H = store.get().holdings; const el = $("#alerts"); if (!el || !H.length) return;
  const out = [];
  for (const h of H) { try { const td = await loadT(h.t); const st = holdStatus(td, h); if (st.st.t !== "정상") out.push(`<a class="item" href="#hold"><span class="pill ${st.st.c}">${esc(st.st.t)}</span><span><b class="mono">${esc(h.t)}</b> <span class="muted" style="font-size:12px">손절 ${fmt(st.stop)} · 현재 ${fmt(st.close)}</span></span><span></span></a>`); } catch (e) { } }
  if (!document.body.contains(el)) return;
  el.hidden = false;
  el.innerHTML = `<h2>보유 종목 점검</h2>${out.length ? `<div class="list">${out.join("")}</div>` : `<div class="ok">보유 ${H.length}종목 모두 정상</div>`}`;
}
function candItem(c, rank) {
  const tgt = c.signal === "풀백" && c.pullback ? `${fmt(c.pullback[0])}~${fmt(c.pullback[1])}` : fmt(c.buyStop);
  return `<a class="item" href="#stock/${encodeURIComponent(c.t)}">
    <span class="row" style="gap:6px;flex-wrap:nowrap">${rank ? `<span class="rank">${rank}</span>` : ""}${ring(c.score, 42)}</span>
    <span style="min-width:0"><span class="t">${esc(c.t)}</span> ${c.signal ? `<span class="pill st2">${esc(c.signal)}</span>` : ""}<div class="n">${c.cap && c.cap !== "기타" ? `<b class="capt">${esc(c.cap)}</b> ` : ""}${esc(c.n)}${c.s ? " · " + esc(c.s) : ""}</div></span>
    <span class="r"><b>${fmt(c.close)}</b><span class="muted" style="font-size:11px">${pct(c.ext)} 이격</span></span>
    <span class="sub"><span>${c.signal === "풀백" ? "풀백" : "매수 스톱"} <b>${tgt}</b></span><span>손절 <b>${fmt(c.stop)}</b> (${pct(c.stopPct)})</span><span>거래량 <b>${c.volGrade}</b> · RS <b>${c.rsGrade}</b>등급</span></span></a>`;
}

/* ---------- 화면: 후보 ---------- */
let candTab = "돌파", capTab = "전체";
function viewCands(v) {
  const tabs = ["돌파", "풀백", "베이스 대기", "65점+"];
  const capOk = c => capTab === "전체" || c.cap === capTab || (capTab === "내 종목" && c.mine);
  const list = SUM.candidates.filter(c => capOk(c) && (candTab === "65점+" ? c.score >= 65 : c.signal === candTab)).sort((a, b) => b.score - a.score);
  const desc = { "돌파": "최근 3주 안에 26주 고점을 거래량과 함께 종가 돌파", "풀백": "2단계 중 30주선(-3~+5%)이나 돌파 지점(±3%)까지 되돌림", "베이스 대기": "1단계 베이스 상단 5% 이내, RS 개선 중", "65점+": "신호와 관계없이 주봉 점수 65점 이상" }[candTab];
  v.innerHTML = `<h1>후보</h1>
    <div class="seg" role="group">${tabs.map(t => `<button data-ct="${t}" aria-pressed="${t === candTab}">${t} ${SUM.candidates.filter(c => capOk(c) && (t === "65점+" ? c.score >= 65 : c.signal === t)).length}</button>`).join("")}</div>
    <div class="seg" role="group">${["전체", "대형", "중형", "소형", "내 종목"].map(t => `<button data-cap="${t}" aria-pressed="${t === capTab}">${t}</button>`).join("")}</div>
    <p class="note">${desc} · 점수 순${SUM.worst === 4 ? " · <b style='color:var(--s4)'>시장 4단계라 신규 매수 보류</b>" : ""}</p>
    ${list.length ? `<div class="list">${list.map(c => candItem(c)).join("")}</div>` : `<div class="empty">해당 종목이 없습니다.</div>`}`;
  v.querySelectorAll("[data-ct]").forEach(b => b.onclick = () => { candTab = b.dataset.ct; viewCands(v); });
  v.querySelectorAll("[data-cap]").forEach(b => b.onclick = () => { capTab = b.dataset.cap; viewCands(v); });
}

/* ---------- 화면: 종목 ---------- */
let chartWeeks = 104;
function searchBox(val = "") {
  return `<div class="search"><input id="q" type="search" placeholder="티커 또는 이름 (예: NVDA)" autocomplete="off" autocapitalize="characters" value="${esc(val)}" aria-label="종목 검색"></div><div class="sugg list" id="sugg"></div>`;
}
function bindSearch(v) {
  const q = $("#q", v), sg = $("#sugg", v);
  const run = () => {
    const s = q.value.trim().toUpperCase(); if (!s) { sg.innerHTML = ""; return; }
    const hits = SUM.index.filter(x => x.t.toUpperCase().startsWith(s) || (x.n || "").toUpperCase().includes(s)).slice(0, 12);
    sg.innerHTML = hits.map(x => `<a class="item" href="#stock/${encodeURIComponent(x.t)}">${pill(x.st)}<span style="min-width:0"><span class="t">${esc(x.t)}</span><div class="n">${esc(x.n)}</div></span><span class="r"><b>${fmt(x.c)}</b>${sgn(x.w1)}</span></a>`).join("") || `<p class="note">찾는 종목이 없습니다. 목록에 없는 종목은 저장소의 my_tickers.txt에 추가하세요.</p>`;
  };
  q.oninput = run;
  q.onkeydown = e => { if (e.key === "Enter") { const a = $("a", sg); if (a) location.hash = a.getAttribute("href"); } };
}
async function viewStock(v, t) {
  if (!t) {
    v.innerHTML = `<h1>종목</h1>${searchBox()}<p class="note">S&P500·나스닥100 전 종목과 업종 ETF를 찾을 수 있어요.</p>`;
    bindSearch(v); $("#q", v).focus(); return;
  }
  v.innerHTML = `${searchBox()}<div class="empty">${esc(t)} 불러오는 중…</div>`;
  bindSearch(v);
  let td;
  try { td = await loadT(t); } catch (e) { v.querySelector(".empty").textContent = `${t} 데이터가 없습니다.`; return; }
  const w = td.w, last = w[w.length - 1], prev = w[w.length - 2];
  const s = store.get().settings;
  const entry = num(td.buyStop) && td.close < td.buyStop ? td.buyStop : td.close;
  const risk = num(td.stop) ? entry - td.stop : null;
  const acct = isKRW(t) ? s.account : s.account / fxRate();   // 계좌(원)를 종목 통화로
  const qty = risk > 0 ? Math.floor(acct * s.riskPct / 100 / risk) : null;
  const qtyAmt = qty ? Math.round(toKRW(qty * entry, t)) : null;
  const viol = td.score != null ? ruleCheck(td) : [];
  const bd = td.breakdown, parts = [["market", "시장", 10], ["sector", "업종", 25], ["stock", "종목 위치", 25], ["volume", "거래량", 15], ["rs", "RS", 15], ["risk", "손절/위험", 10]];
  const held = store.get().holdings.find(h => h.t === t);
  v.innerHTML = `${searchBox()}
  <section class="card">
    <div class="row between" style="align-items:flex-start;flex-wrap:nowrap">
      <div style="min-width:0"><h1 class="mono">${esc(td.t)}</h1><div class="muted" style="font-size:13px">${esc(td.name)}${td.sector ? " · " + esc(td.sector) : ""}</div>
        <div class="row" style="margin-top:6px">${pill(td.stage, td.label)}${td.signal ? `<span class="pill st2">${esc(td.signal)}</span>` : ""}</div></div>
      ${td.score != null ? ring(td.score, 76) : ""}
    </div>
    <div class="row"><b class="mono" style="font-size:22px">${fmt(last[W.c])}</b>${sgn(prev ? (last[W.c] / prev[W.c] - 1) * 100 : null)}<span class="muted" style="font-size:12px">주간 · ${last[W.d]} 주</span></div>
    <div class="seg" role="group">${[[52, "1년"], [104, "2년"], [156, "3년"]].map(([n, l]) => `<button data-wk="${n}" aria-pressed="${n === chartWeeks}">${l}</button>`).join("")}</div>
    <div><div class="chartinfo"></div><div class="chartbox" id="chart"></div></div>
    <p class="note"><span style="color:var(--ma)">━</span> 30주선 · 회색 막대 거래량 · 차트를 누르거나 끌면 그 주 값이 위에 나와요</p>
  </section>
  ${td.score != null ? `<section class="card"><h3>매수 계획</h3>
    <div class="kv">
      <div><span>매수 스톱</span><b style="color:var(--s2)">${td.close >= (td.buyStop || Infinity) ? "돌파함" : fmt(td.buyStop)}</b><small>${num(td.buyStop) && td.close < td.buyStop ? pct((td.buyStop / td.close - 1) * 100) : ""}</small></div>
      <div><span>풀백 구간</span><b style="color:var(--s2)">${td.pullback ? fmt(td.pullback[0]) + "~<wbr>" + fmt(td.pullback[1]) : "–"}</b></div>
      <div><span>손절</span><b style="color:var(--s4)">${fmt(td.stop)}</b><small>${pct(td.stopPct)}</small></div>
      <div><span>30주선 이격</span><b style="${td.ext >= 20 ? "color:var(--s4)" : ""}">${pct(td.ext)}</b></div>
      <div><span>추천 수량 (${s.riskPct}% 위험)</span><b>${qty ? qty.toLocaleString() + "주" : "–"}</b><small>${qtyAmt ? "약 " + (qtyAmt / 10000).toFixed(0) + "만원" : ""}</small></div>
    </div>
    <p class="note">추천 수량: ${isKRW(t) ? "" : "환율 " + fmt(fxRate(), 0) + "원 기준, "}손절 시 계좌 ${Math.round(s.account / 10000).toLocaleString()}만원의 ${s.riskPct}%만 잃는 수량${qtyAmt && qtyAmt > s.account * 0.25 ? " · 계좌의 25%를 넘으니 줄이세요" : ""}</p>
    ${viol.length ? `<div class="warn">규칙 점검: ${viol.map(esc).join(" · ")}</div>` : `<div class="ok">와인스테인 기본 조건 통과</div>`}
    <div class="btns">${held ? `<a class="btn" href="#hold">보유 중 · 손절 관리 보기</a>` : `<button class="btn primary" id="buyBtn">매수 기록</button>`}</div>
  </section>
  <section class="card"><h3>점수 구성</h3>
    <div class="bars">${parts.map(([k, l, m]) => { const val = bd?.[k], wd = val == null ? 0 : Math.min(1, val / m) * 100, c = wd >= 75 ? "var(--s2)" : wd >= 50 ? "#5f8a2c" : wd >= 25 ? "var(--s3)" : "var(--s4)"; return `<div class="brow"><span>${l}</span><div class="track"><div class="fill" style="width:${wd}%;background:${c}"></div></div><span class="v"><b>${val ?? "–"}</b>/${m}</span></div>`; }).join("")}
    ${grade9("거래량", td.volGrade)}${grade9("RS", td.rsGrade)}</div>
    ${td.caps?.length ? `<p class="note">상한·감점: ${td.caps.map(esc).join(", ")}</p>` : ""}
    <p class="note">자동 계산 점수입니다. 사기 전에 프로젝트에 차트를 보내 최종 확인하세요.</p>
  </section>` : `<section class="card"><p class="note">업종 ETF·지수는 단계만 표시합니다. 30주선 이격 ${pct(td.ext)}</p></section>`}`;
  bindSearch(v);
  const box = $("#chart", v);
  const draw = () => drawChart(box, td, chartWeeks);
  draw();
  v.querySelectorAll("[data-wk]").forEach(b => b.onclick = () => { chartWeeks = +b.dataset.wk; v.querySelectorAll("[data-wk]").forEach(x => x.setAttribute("aria-pressed", x === b)); draw(); });
  window.onresize = () => { if (document.body.contains(box)) draw(); };
  const bb = $("#buyBtn", v); if (bb) bb.onclick = () => buySheet(td, qty, viol);
}

/* ---------- 시트(입력창) ---------- */
function openSheet(html, bind) {
  const sh = $("#sheet"); sh.innerHTML = `<div class="panel" role="dialog" aria-modal="true">${html}</div>`; sh.hidden = false;
  sh.onclick = e => { if (e.target === sh) closeSheet(); };
  bind(sh);
  const f = $("input", sh); if (f) setTimeout(() => f.focus(), 50);
}
function closeSheet() { const sh = $("#sheet"); sh.hidden = true; sh.innerHTML = ""; }
function buySheet(td, qty, viol, edit) {
  const h = edit || { buyDate: today(), buyPrice: td.close, initStop: td.stop, shares: qty || "" };
  openSheet(`<h2>${edit ? "보유 수정" : "매수 기록"} · <span class="mono">${esc(td.t)}</span></h2>
    ${viol?.length && !edit ? `<div class="warn">규칙 점검: ${viol.map(esc).join(" · ")}<br>그래도 기록하면 일지에 '규칙 위반'으로 표시돼요.</div>` : ""}
    <form class="form" id="bf">
      <div class="grid2"><label>매수일<input id="f_date" type="date" required value="${esc(h.buyDate)}"></label>
      <label>매수가<input id="f_price" type="number" step="any" inputmode="decimal" required value="${esc(h.buyPrice)}"></label></div>
      <div class="grid2"><label>초기 손절가<input id="f_stop" type="number" step="any" inputmode="decimal" required value="${esc(fmt(h.initStop).replace(/,/g, ""))}"></label>
      <label>수량<input id="f_sh" type="number" step="any" inputmode="decimal" required value="${esc(h.shares)}"></label></div>
      <label>메모<input id="f_note" type="text" maxlength="80" value="${esc(h.note || "")}" placeholder="예: 돌파 매수 절반"></label>
      <p class="note" id="f_err" style="color:var(--s4)"></p>
      <div class="btns"><button class="btn primary" type="submit">${edit ? "저장" : "보유에 추가"}</button><button class="btn" type="button" id="f_cancel">취소</button></div>
    </form>`, sh => {
    $("#f_cancel", sh).onclick = closeSheet;
    $("#bf", sh).onsubmit = e => {
      e.preventDefault();
      const price = +$("#f_price", sh).value, stop = +$("#f_stop", sh).value, sh_ = +$("#f_sh", sh).value;
      if (!(stop < price)) { $("#f_err", sh).textContent = "손절가는 매수가보다 낮아야 해요."; return; }
      if (!(sh_ > 0)) { $("#f_err", sh).textContent = "수량을 입력하세요."; return; }
      const d = store.get();
      const rec = { id: edit?.id || uid(), t: td.t, buyDate: $("#f_date", sh).value, buyPrice: price, initStop: stop, shares: sh_, note: $("#f_note", sh).value.trim(), violations: edit ? edit.violations : (viol || []), scoreAtBuy: edit ? edit.scoreAtBuy : td.score };
      if (edit) d.holdings = d.holdings.map(x => x.id === edit.id ? rec : x); else d.holdings.push(rec);
      store.save(); closeSheet(); toast(edit ? "저장했어요" : "보유에 추가했어요"); go("#hold");
    };
  });
}
function sellSheet(h, close) {
  openSheet(`<h2>매도 기록 · <span class="mono">${esc(h.t)}</span></h2>
    <form class="form" id="sf">
      <div class="grid2"><label>매도일<input id="s_date" type="date" required value="${today()}"></label>
      <label>매도가<input id="s_price" type="number" step="any" inputmode="decimal" required value="${esc(fmt(close).replace(/,/g, ""))}"></label></div>
      <label>수량 (일부 매도 가능)<input id="s_sh" type="number" step="any" inputmode="decimal" required value="${esc(h.shares)}"></label>
      <label>이유<select id="s_why"><option>손절</option><option>추적 손절</option><option>4단계 진입</option><option>일부 차익</option><option>규칙 외 매도</option></select></label>
      <p class="note" id="s_err" style="color:var(--s4)"></p>
      <div class="btns"><button class="btn primary" type="submit">일지로 옮기기</button><button class="btn" type="button" id="s_cancel">취소</button></div>
    </form>`, sh => {
    $("#s_cancel", sh).onclick = closeSheet;
    $("#sf", sh).onsubmit = e => {
      e.preventDefault();
      const n = +$("#s_sh", sh).value; if (!(n > 0 && n <= h.shares)) { $("#s_err", sh).textContent = `수량은 0보다 크고 ${h.shares} 이하로 입력하세요.`; return; }
      const d = store.get();
      d.trades.push({ ...h, id: uid(), shares: n, sellDate: $("#s_date", sh).value, sellPrice: +$("#s_price", sh).value, reason: $("#s_why", sh).value });
      if (n >= h.shares) d.holdings = d.holdings.filter(x => x.id !== h.id); else d.holdings = d.holdings.map(x => x.id === h.id ? { ...x, shares: +(x.shares - n).toFixed(6) } : x);
      store.save(); closeSheet(); toast("일지에 기록했어요"); route();
    };
  });
}
function confirmSheet(msg, onYes) {
  openSheet(`<h2>${esc(msg)}</h2><div class="btns"><button class="btn danger" id="c_yes">삭제</button><button class="btn" id="c_no">취소</button></div>`, sh => {
    $("#c_no", sh).onclick = closeSheet; $("#c_yes", sh).onclick = () => { onYes(); closeSheet(); };
  });
}

/* ---------- 화면: 보유 ---------- */
async function viewHold(v) {
  const d = store.get(), H = d.holdings;
  v.innerHTML = `<div class="row between"><h1>보유</h1><a class="btn sm" href="#settings">설정·백업</a></div>
    <p class="note">손절선은 매주 자동으로 올라가요: 초기 손절 → 수익이 손절폭만큼 나거나 3주 이상 수익이면 본전 → 30주선·최근 8주 저점 아래로 추적. 내리지 않습니다.</p>
    ${H.length > d.settings.maxPos ? `<div class="warn">보유 ${H.length}종목 · 설정한 최대 ${d.settings.maxPos}종목을 넘었어요</div>` : ""}
    <div class="list" id="hl">${H.length ? H.map(h => `<div class="card" data-id="${h.id}"><div class="muted">${esc(h.t)} 계산 중…</div></div>`).join("") : `<div class="empty">보유 종목이 없습니다.<br>종목 화면에서 '매수 기록'을 누르면 여기서 손절선을 관리해요.</div>`}</div>`;
  for (const h of H) {
    const el = v.querySelector(`[data-id="${h.id}"]`);
    try {
      const td = await loadT(h.t), st = holdStatus(td, h);
      if (!document.body.contains(el)) return;
      el.innerHTML = `<div class="row between"><a href="#stock/${encodeURIComponent(h.t)}" class="row" style="text-decoration:none"><b class="mono" style="font-size:17px">${esc(h.t)}</b>${pill(td.stage, td.label)}</a><span class="pill ${st.st.c}">${esc(st.st.t)}</span></div>
        <div class="kv kv4">
          <div><span>현재가</span><b>${fmt(st.close)}</b><small>매수 대비 ${pct((st.close / h.buyPrice - 1) * 100)}</small></div>
          <div><span>${st.broke ? "깬 손절선" : "지금 손절선"}</span><b style="color:var(--s4)">${fmt(st.stop)}</b><small>${esc(st.phase)}</small></div>
          <div><span>${st.broke ? "손절가 매도 시" : "손절까지"}</span><b>${st.broke ? sgn(st.pl) : pct(-st.dist)}</b><small>${st.broke ? "" : "현재가 기준"}</small></div>
          <div><span>손익(R)</span><b class="${st.R >= 0 ? "pos" : "neg"}">${st.R == null ? "–" : st.R.toFixed(2) + "R"}</b><small>${st.held}주 보유</small></div>
        </div>
        ${st.broke ? `<div class="warn">${st.broke} 주에 손절선 ${fmt(st.stop)}을 깼어요. 팔았다면 매도 기록을 남기세요.</div>` : ""}
        <p class="note">매수 ${esc(h.buyDate)} · ${fmt(h.buyPrice)} × ${h.shares} · 초기 손절 ${fmt(h.initStop)}${h.note ? " · " + esc(h.note) : ""}</p>
        <div class="btns"><button class="btn sm primary" data-sell>매도 기록</button><button class="btn sm" data-edit>수정</button><button class="btn sm danger" data-del>삭제</button></div>`;
      $("[data-sell]", el).onclick = () => sellSheet(h, st.close);
      $("[data-edit]", el).onclick = () => buySheet(td, null, null, h);
      $("[data-del]", el).onclick = () => confirmSheet(`${h.t} 보유 기록을 삭제할까요?`, () => { const d2 = store.get(); d2.holdings = d2.holdings.filter(x => x.id !== h.id); store.save(); route(); });
    } catch (e) { el.innerHTML = `<b class="mono">${esc(h.t)}</b><p class="note">이 종목 데이터가 없습니다. 저장소의 my_tickers.txt에 추가하면 다음 갱신부터 계산돼요.</p>`; }
  }
}

/* ---------- 화면: 일지 ---------- */
async function benchRet(t, from, to) {
  try { const td = await loadT(t), w = td.w; const a = weekIndex(w, from), b = weekIndex(w, to); if (a < 0 || b < 0) return null; return (w[b][W.c] / w[a][W.c] - 1) * 100; } catch (e) { return null; }
}
async function viewJournal(v) {
  const T = store.get().trades.slice().sort((a, b) => b.sellDate.localeCompare(a.sellDate));
  v.innerHTML = `<div class="row between"><h1>매매일지</h1><a class="btn sm" href="#settings">설정·백업</a></div><div id="js"><div class="empty">계산 중…</div></div>`;
  if (!T.length) { $("#js", v).innerHTML = `<div class="empty">아직 매도한 거래가 없습니다.<br>보유 화면에서 '매도 기록'을 하면 여기에 쌓여요.</div>`; return; }
  const rows = [];
  for (const t of T) {
    const ret = (t.sellPrice / t.buyPrice - 1) * 100, risk = t.buyPrice - t.initStop, R = risk > 0 ? (t.sellPrice - t.buyPrice) / risk : null;
    const spy = await benchRet("SPY", t.buyDate, t.sellDate), qqq = await benchRet("QQQ", t.buyDate, t.sellDate);
    rows.push({ ...t, ret, R, pnl: toKRW((t.sellPrice - t.buyPrice) * t.shares, t.t), spy, qqq, bad: (t.violations || []).length > 0 || t.reason === "규칙 외 매도" });
  }
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  const wins = rows.filter(r => r.ret > 0), Rs = rows.map(r => r.R).filter(x => x != null);
  const good = rows.filter(r => !r.bad), bad = rows.filter(r => r.bad);
  const sw = wins.reduce((s, r) => s + (r.R || 0), 0), sl = rows.filter(r => r.ret <= 0).reduce((s, r) => s + (r.R || 0), 0);
  const aS = avg(rows.map(r => r.spy).filter(x => x != null)), aQ = avg(rows.map(r => r.qqq).filter(x => x != null));
  $("#js", v).innerHTML = `
    <div class="stat">
      <div><span>거래 수 · 승률</span><b>${rows.length} · ${Math.round(wins.length / rows.length * 100)}%</b></div>
      <div><span>평균 손익</span><b>${Rs.length ? avg(Rs).toFixed(2) + "R" : "–"}</b></div>
      <div><span>평균 수익률</span><b>${sgn(avg(rows.map(r => r.ret)))}</b></div>
      <div><span>수익 팩터</span><b>${sl < 0 ? (sw / -sl).toFixed(2) : "–"}</b></div>
      <div><span>실현 손익 (원, 현재 환율)</span><b class="${rows.reduce((s, r) => s + r.pnl, 0) >= 0 ? "pos" : "neg"}">${Math.round(rows.reduce((s, r) => s + r.pnl, 0)).toLocaleString()}</b></div>
    </div>
    <section class="card"><h3>같은 기간 지수를 들고 있었다면</h3>
      <div class="kv"><div><span>내 거래 평균</span><b>${sgn(avg(rows.map(r => r.ret)))}</b></div><div><span>SPY 평균</span><b>${sgn(aS)}</b></div><div><span>QQQ 평균</span><b>${sgn(aQ)}</b></div></div>
      <p class="note">각 거래의 매수일~매도일 동안 SPY·QQQ 수익률을 주봉 종가로 근사해 평균했어요.</p></section>
    <section class="card"><h3>규칙을 지켰을 때 vs 어겼을 때</h3>
      <div class="kv"><div><span>지킨 거래 ${good.length}건</span><b>${good.length ? avg(good.map(r => r.R ?? 0)).toFixed(2) + "R" : "–"}</b></div><div><span>어긴 거래 ${bad.length}건</span><b>${bad.length ? avg(bad.map(r => r.R ?? 0)).toFixed(2) + "R" : "–"}</b></div></div></section>
    <div class="list">${rows.map(r => `<div class="item"><span class="mono" style="font-weight:800">${esc(r.t)}</span>
      <span style="min-width:0"><span class="muted" style="font-size:12px">${esc(r.buyDate)} → ${esc(r.sellDate)} · ${esc(r.reason)}</span>${r.bad ? ` <span class="tag">규칙 위반</span>` : ""}</span>
      <span class="r"><b>${sgn(r.ret)}</b><span class="muted" style="font-size:11px">${r.R == null ? "" : r.R.toFixed(2) + "R"}</span></span>
      <span class="sub"><span>${fmt(r.buyPrice)} → ${fmt(r.sellPrice)} × ${r.shares}</span><span>SPY ${pct(r.spy)} · QQQ ${pct(r.qqq)}</span><button class="btn sm danger" data-deltr="${r.id}" style="padding:2px 8px">삭제</button></span></div>`).join("")}</div>`;
  v.querySelectorAll("[data-deltr]").forEach(b => b.onclick = () => confirmSheet("이 거래 기록을 삭제할까요?", () => { const d = store.get(); d.trades = d.trades.filter(x => x.id !== b.dataset.deltr); store.save(); route(); }));
}

/* ---------- 화면: 설정·백업 ---------- */
function viewSettings(v) {
  const s = store.get().settings;
  v.innerHTML = `<h1>설정·백업</h1>
  <section class="card"><form class="form" id="setf">
    <label>계좌 금액 (원)<input id="g_acc" type="number" inputmode="numeric" value="${esc(s.account)}"></label>
    <div class="grid2"><label>한 번 손절 시 계좌 손실<select id="g_risk">${[0.5, 1, 1.5, 2].map(x => `<option value="${x}" ${+s.riskPct === x ? "selected" : ""}>${x}%</option>`).join("")}</select></label>
    <label>최대 보유 종목<input id="g_max" type="number" min="1" max="20" value="${esc(s.maxPos)}"></label></div>
    <button class="btn primary" type="submit">저장</button></form></section>
  <section class="card"><h3>백업</h3>
    <p class="note">보유 종목과 매매일지는 이 휴대폰 브라우저에만 저장돼요. 앱을 지우거나 브라우저 데이터를 지우면 사라지니 가끔 백업 파일을 받아 두세요.</p>
    <div class="btns"><button class="btn" id="exp">백업 파일 받기</button><label class="btn" for="imp">백업 불러오기</label><input id="imp" type="file" accept="application/json,.json" hidden></div></section>
  <section class="card"><h3>백테스트</h3>
    <p class="note" id="btinfo">결과 확인 중…</p>
    <div class="btns"><a class="btn" id="btlink" href="backtest/" hidden>백테스트 결과 보기</a></div>
    <p class="note">새로 돌리려면 GitHub 저장소 → Actions → backtest → Run workflow. 시작일과 점수 기준을 바꿔 돌릴 수 있고, 15~30분 뒤 여기에 결과가 바뀌어요.</p></section>
  <section class="card"><h3>이 앱</h3><p class="note">시세는 Yahoo Finance 무료 데이터로, 미국장 마감 후 하루 한 번 갱신돼요. 점수는 와인스테인 규칙을 기계적으로 적용한 값이며 투자 조언이 아니고 수익을 보장하지 않습니다.</p></section>`;
  fetch("backtest/", { method: "HEAD", cache: "no-store" }).then(r => {
    const el = $("#btinfo", v); if (!el) return;
    if (r.ok) { const lm = r.headers.get("last-modified"); el.textContent = "최근 결과" + (lm ? " · " + new Date(lm).toLocaleDateString("ko-KR") : ""); $("#btlink", v).hidden = false; }
    else el.textContent = "아직 백테스트 결과가 없어요.";
  }).catch(() => { const el = $("#btinfo", v); if (el) el.textContent = "결과를 확인하지 못했어요."; });
  $("#setf", v).onsubmit = e => { e.preventDefault(); const d = store.get(); d.settings = { account: +$("#g_acc", v).value || 0, riskPct: +$("#g_risk", v).value, maxPos: +$("#g_max", v).value || 6 }; store.save(); toast("저장했어요"); };
  $("#exp", v).onclick = () => { const blob = new Blob([JSON.stringify(store.get(), null, 1)], { type: "application/json" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `stage-backup-${today()}.json`; document.body.appendChild(a); a.click(); a.remove(); };
  $("#imp", v).onchange = async e => { const f = e.target.files[0]; if (!f) return; try { const d = JSON.parse(await f.text()); if (!Array.isArray(d.holdings) || !Array.isArray(d.trades)) throw 0; store.replace(d); toast(`불러왔어요 · 보유 ${d.holdings.length} · 거래 ${d.trades.length}`); } catch (_) { toast("백업 파일 형식이 아니에요"); } };
}

/* ---------- 라우터 ---------- */
async function route() {
  const v = $("#view");
  const [tab, arg] = (location.hash.slice(1) || "home").split("/");
  document.querySelectorAll(".tabs a").forEach(a => a.toggleAttribute("aria-current", false));
  const cur = document.querySelector(`.tabs a[data-tab="${tab === "settings" ? "hold" : tab}"]`); if (cur) cur.setAttribute("aria-current", "page");
  if (!SUM) { try { await loadSummary(); } catch (e) { v.innerHTML = `<div class="empty">데이터를 불러오지 못했어요.<br>아직 첫 갱신 전이거나 인터넷 연결이 끊겼어요.</div>`; $("#updated").textContent = "데이터 없음"; return; } }
  window.onresize = null;
  try {
    if (tab === "cands") viewCands(v);
    else if (tab === "stock") await viewStock(v, arg ? decodeURIComponent(arg).toUpperCase() : "");
    else if (tab === "hold") await viewHold(v);
    else if (tab === "journal") await viewJournal(v);
    else if (tab === "settings") viewSettings(v);
    else await viewHome(v);
  } catch (e) { console.error(e); v.innerHTML = `<div class="empty">화면을 그리다 문제가 생겼어요. 새로고침해 보세요.</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => { });

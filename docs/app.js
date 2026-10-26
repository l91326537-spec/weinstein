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
  /* 백테스트에서 가장 나았던 규칙: 초기 손절(돌파 -5%, 풀백 = 3주 저점·30주선 아래)만 두고 4단계가 될 때까지 보유 */
  const buy = +h.buyPrice, s0 = +h.initStop;
  let broke = null;
  const k = weekIndex(w, h.buyDate);
  if (k < 0) return { stop: s0, phase: "초기 손절", broke, held: 0, risk: buy - s0 };
  for (let j = k + 1; j < w.length; j++) if (w[j][W.l] <= s0) { broke = w[j][W.d]; break; }
  return { stop: s0, phase: "초기 손절 · 4단계까지 보유", broke, held: w.length - 1 - k, risk: buy - s0 };
}
function holdStatus(td, h) {
  const w = td.w, last = w[w.length - 1], close = last[W.c];
  const tr = trailStop(w, h);
  const dist = (close - tr.stop) / close * 100;
  let st = { t: "정상", c: "st2" };
  if (tr.broke) st = { t: "손절 이탈", c: "st4" };
  else if (td.stage === 4) st = { t: "4단계 · 매도", c: "st4" };
  else if (dist < 3) st = { t: "손절 근접", c: "st3" };
  else if (td.stage === 3) st = { t: "3단계 경고", c: "st3" };
  const ref = tr.broke ? tr.stop : close;     // 손절을 깼으면 손절가에 판 것으로 계산
  const pl = (ref / h.buyPrice - 1) * 100, R = tr.risk > 0 ? (ref - h.buyPrice) / tr.risk : null;
  return { ...tr, close, dist, st, pl, R };
}

/* ---------- 규칙 점검 (매수 기록 시) ---------- */
/* 시가총액: 원화(조·억)로, 환율 없으면 달러 */
function mcTxt(mc) {
  if (!mc) return "";
  const fx = SUM && SUM.fx;
  if (!fx) return mc >= 1e9 ? `$${(mc / 1e9).toFixed(mc >= 1e11 ? 0 : 1)}B` : `$${Math.round(mc / 1e6)}M`;
  const k = mc * fx;
  return k >= 1e12 ? `${(k / 1e12).toFixed(k >= 1e13 ? 0 : 1)}조` : `${Math.round(k / 1e8).toLocaleString()}억`;
}
function capTag(cap, mc) {
  const c = cap && cap !== "기타" ? `<b class="capt">${esc(cap)}</b> ` : "";
  const m = mcTxt(mc);
  return c + (m ? `<span class="muted">시총 ${m}</span> · ` : "");
}
/* 업종 ETF 화면: 그 업종에 속한 종목 (점수 순) */
let secCapAll = false;
function secMembers(t) {
  const sec = (SUM.sectors || []).find(x => x.etf === t);
  if (!sec || !sec.mem || !sec.mem.length) return "";
  const rows = sec.mem.filter(m => secCapAll || m[4] !== "소형");
  const nSmall = sec.mem.filter(m => m[4] === "소형").length;
  return `<section class="card" id="secmem"><div class="row between"><h2>${esc(sec.name)} 종목 ${rows.length}</h2>
    ${nSmall ? `<button class="btn sm" id="secsmall">${secCapAll ? "소형주 숨기기" : `소형주 ${nSmall}개 보기`}</button>` : ""}</div>
    <p class="note">점수 순 · 업종 ${sec.stage}단계${sec.stage === 2 ? " (좋음)" : sec.stage >= 3 ? " (조심)" : ""}</p>
    <div class="list">${rows.map(([tk, sc, st, sg, cap, ext, nm, mc]) => `<a class="item" href="#stock/${encodeURIComponent(tk)}">${pill(st)}
      <span style="min-width:0"><span class="t">${esc(tk)}</span> ${sg ? `<span class="pill st2">${esc(sg)}</span>` : ""}
      <div class="n">${capTag(cap, mc)}${esc(nm)} · 이격 ${ext ?? "–"}%</div></span>
      <span class="r"><b>${sc}</b><span class="muted" style="font-size:11px">점</span></span></a>`).join("")}</div></section>`;
}
/* 점수가 낮은 항목의 이유 (점수 구성 아래 한 줄) */
function lowWhy(td) {
  const bd = td.breakdown || {}, out = [], ext = td.ext;
  if (bd.stock != null && bd.stock <= 3) out.push(td.stage !== 2 && td.stage !== 1 ? `진입 위치 ${bd.stock}: ${td.stage}단계라 매수 자리 아님` : num(ext) !== null && ext > 15 ? `진입 위치 ${bd.stock}: 30주선보다 ${Math.round(ext)}% 위라 너무 올라 있음 (돌파 직후나 30주선 근처가 아님)` : `진입 위치 ${bd.stock}: 돌파·풀백 자리가 아님`);
  if (bd.risk != null && bd.risk <= 2) out.push(`손절폭 ${bd.risk}: 지금 사면 손절선(${fmt(td.stop)})까지 ${td.close && td.stop ? Math.round((1 - td.stop / td.close) * 100) : "?"}%나 떨어져야 해서 위험이 큼`);
  if (bd.sector != null && bd.sector <= 3) out.push(`업종 ${bd.sector}: 업종 ETF가 ${td.sectorStage ?? "?"}단계`);
  if (bd.volume != null && bd.volume <= 5) out.push(`거래량 ${bd.volume}: 오르는 주보다 내리는 주 거래량이 많음`);
  if (bd.rs != null && bd.rs <= 3) out.push(`RS ${bd.rs}: 시장보다 약함`);
  return out;
}
/* 매수 점수 기준: 백테스트(2006~)에서 80점 이상만 산 경우가 70점보다 앞·뒤 기간 모두 나았음 */
const BUY_SCORE = 80;
function ruleCheck(td) {
  const v = [];
  if (SUM && SUM.worst === 4) v.push("시장 4단계");
  if (td.score == null || td.score < BUY_SCORE) v.push(`점수 ${BUY_SCORE} 미만`);
  if (td.stage !== 2) v.push("2단계 아님");
  if ([3, 4].includes(td.sectorStage)) v.push("업종 " + td.sectorStage + "단계");
  if (num(td.ext) !== null && td.ext >= 20) v.push("과열(이격 20%+)");
  if (td.cap === "소형" && td.signal === "돌파" && !(td.brkVr >= 2)) v.push("소형주 돌파 거래량 2배 미만");
  /* 백테스트는 손절폭 20% 넘는 자리는 사지 않았음 */
  const st_ = td.signal === "풀백" ? td.pbStop : td.signal === "돌파" ? td.bkStop : null;
  const en_ = td.signal === "돌파" && td.bkBuy && td.close < td.bkBuy ? td.bkBuy : td.close;
  if (num(st_) !== null && en_ > 0 && (1 - st_ / en_) * 100 > 20) v.push(`손절폭 ${Math.round((1 - st_ / en_) * 100)}% (20% 초과 · 매수 부적합)`);
  return v;
}

/* ---------- 차트 (SVG) ---------- */
/* 보이는 구간: n주, end = 마지막 주 다음 인덱스 (null = 최신까지). 확대·이동은 이 값만 바꾸고 다시 그림 */
const CHART_MIN = 12;
function chartWin(td, view) {
  const total = td.w.length, n = Math.max(Math.min(CHART_MIN, total), Math.min(total, Math.round(view.n)));
  const end = Math.max(n, Math.min(total, view.end == null ? total : Math.round(view.end)));
  return { n, end, start: end - n, total };
}
function drawChart(box, td, view) {
  const cw = chartWin(td, view);
  const w = td.w.slice(cw.start, cw.end), n = w.length;
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
  const pb = td.signal === "풀백" && td.pbStop;     /* 매수 계획 칸과 같은 값: 풀백이면 풀백 손절, 아니면 돌파 매수가·돌파 손절 */
  const lines = [[pb ? null : (td.bkBuy || td.buyStop), "var(--s2)", "매수"], [pb ? td.pbStop : (td.bkStop || td.stop), "var(--s4)", "손절"]];
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
  box._geo = { W_, plotW, n, show };
  show(n - 1);
}
/* 확대·이동: 두 손가락 벌리기/오므리기(+옆으로 끌면 이동), 마우스 휠, 버튼. 한 손가락은 지금처럼 그 주 값 보기 */
function bindChartZoom(box, td, view, redraw) {
  const pts = new Map();
  let pinch = null, raf = 0;
  const frac = clientX => { const b = box.getBoundingClientRect(), g = box._geo; return Math.max(0, Math.min(1, ((clientX - b.left) * g.W_ / b.width - 2) / g.plotW)); };
  const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; redraw(); }); };
  const setWin = (n, anchorIdx, anchorFrac) => {
    const total = td.w.length;
    n = Math.max(Math.min(CHART_MIN, total), Math.min(total, n));
    let end = anchorIdx - anchorFrac * n + n;
    end = Math.max(n, Math.min(total, end));
    view.n = n; view.end = end >= total - 0.5 ? null : end;
  };
  const pick = e => { const g = box._geo; if (g) g.show(Math.max(0, Math.min(g.n - 1, Math.floor(frac(e.clientX) * g.n)))); };
  box.addEventListener("pointerdown", e => {
    pts.set(e.pointerId, e.clientX);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()], cw = chartWin(td, view), mid = (a + b) / 2;
      pinch = { d0: Math.max(20, Math.abs(a - b)), n0: cw.n, anchor: cw.start + frac(mid) * cw.n };
    } else pick(e);
  });
  box.addEventListener("pointermove", e => {
    if (!pts.has(e.pointerId)) { if (e.pointerType === "mouse") pick(e); return; }
    pts.set(e.pointerId, e.clientX);
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()];
      setWin(pinch.n0 * pinch.d0 / Math.max(20, Math.abs(a - b)), pinch.anchor, frac((a + b) / 2));
      schedule();
    } else if (!pinch) pick(e);
  });
  const up = e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; };
  box.addEventListener("pointerup", up); box.addEventListener("pointercancel", up); box.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") up(e); });
  box.addEventListener("wheel", e => {
    e.preventDefault();
    const cw = chartWin(td, view), f = frac(e.clientX);
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) setWin(cw.n, cw.start + f * cw.n + e.deltaX / 40, f);      // 가로 스크롤 = 이동
    else setWin(cw.n * (e.deltaY > 0 ? 1.15 : 1 / 1.15), cw.start + f * cw.n, f);
    schedule();
  }, { passive: false });
}
function chartStep(td, view, act) {
  const cw = chartWin(td, view), total = td.w.length;
  if (act === "in" || act === "out") { const n = Math.round(cw.n * (act === "in" ? 0.67 : 1.5)); view.n = Math.max(Math.min(CHART_MIN, total), Math.min(total, n)); if (view.end != null) view.end = Math.min(total, Math.max(view.n, cw.end)); }
  else if (act === "left" || act === "right") { const step = Math.max(1, Math.round(cw.n / 3)), end = cw.end + (act === "left" ? -step : step); view.end = end >= total ? null : Math.max(cw.n, end); }
  else if (act === "now") view.end = null;
}

/* ---------- 화면: 홈 ---------- */
/* 시장 위험 신호 (참고): 30주선 위 종목 비율, 신고가-신저가, 업종 단계 분포. 매매 규칙에는 쓰지 않음 */
function breadthCard(b) {
  if (!b) return "";
  const h = (b.hist || []).filter(x => x != null), W = 300, H = 90;
  const y = x => H - 4 - x / 100 * (H - 8), dx = h.length > 1 ? W / (h.length - 1) : 0;
  const path = h.map((x, i) => `${i ? "L" : "M"}${(i * dx).toFixed(1)},${y(x).toFixed(1)}`).join("");
  const svg = h.length > 1 ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:90px" aria-label="30주선 위 종목 비율 52주">
    <line x1="0" x2="${W}" y1="${y(40)}" y2="${y(40)}" stroke="var(--s4)" stroke-dasharray="4 4" stroke-width="1"/>
    <line x1="0" x2="${W}" y1="${y(60)}" y2="${y(60)}" stroke="var(--s2)" stroke-dasharray="4 4" stroke-width="1"/>
    <path d="${path}" fill="none" stroke="var(--fg)" stroke-width="1.6"/></svg>` : "";
  const tone = b.warn && b.warn.length ? "warn" : "ok";
  return `<section class="card"><div class="row between"><h3>시장 위험 신호</h3><span class="tag ${tone === "ok" ? "good" : ""}">${tone === "ok" ? "특이 신호 없음" : `경고 ${b.warn.length}개`}</span></div>
    <div class="kv"><div><span>30주선 위 종목</span><b>${b.pa ?? "–"}%</b><small>10주 평균 ${b.pa10 ?? "–"}%</small></div>
      <div><span>신고가−신저가</span><b>${b.nhnl4 == null ? "–" : (b.nhnl4 > 0 ? "+" : "") + b.nhnl4}%</b><small>4주 평균</small></div>
      <div><span>업종 2단계/4단계</span><b>${b.sec2}/${b.sec4}</b><small>전체 ${b.secN}개</small></div></div>
    ${svg}<p class="note">30주선 위 종목 비율 52주 (점선: 위 60%, 아래 40%)</p>
    ${(b.warn || []).map(w => `<div class="warn">${esc(w)}</div>`).join("")}
    <p class="note">와인스타인이 시장을 볼 때 쓴 '시장 폭' 지표예요. 참고용이며 매매 규칙에는 쓰지 않아요. 백테스트에서 이 지표로 QQQ를 미리 사고팔면 지금 규칙(지수 4단계면 매도)보다 일관되게 낫지 않았어요. 여러 경고가 함께 뜨면 새 매수를 조심스럽게 보세요.</p></section>`;
}
async function viewHome(v) {
  const mk = SUM.market, worst = SUM.worst;
  const mrow = Object.entries(mk).filter(([, m]) => m).map(([t, m]) => `<div class="row between"><span>${esc(m.name)}</span><span class="row">${pill(m.stage, m.label)}<span class="mono muted" style="font-size:12px">30주선 ${pct(m.ext)}</span></span></div>`).join("");
  const top = SUM.candidates.filter(c => c.signal && c.cap !== "소형").sort((a, b) => b.score - a.score).slice(0, 5);
  const secs = SUM.sectors;
  v.innerHTML = `
  <a class="card item" href="#orders" style="text-decoration:none"><span class="pill st2">주문</span><span style="min-width:0"><b>이번 주 주문</b><div class="n">${(() => { const a = watchAge(store.get().settings); return a != null && a >= WATCH_DAYS - 5 ? `<b style="color:var(--s4)">자동감시주문 만료 임박 (${a}일 전 설정)</b>` : "매수·매도·손절 주문, 키움 자동감시 설정값 · 보내기/복사"; })()}</div></span><span class="r">›</span></a>
  <a class="card item" href="#guide" style="text-decoration:none"><span class="pill st1">정리</span><span style="min-width:0"><b>원칙·백테스트 정리</b><div class="n">4단계, 매수·매도 원칙, 백테스트로 확인한 것</div></span><span class="r">›</span></a>
  <section class="card"><h3>시장</h3>${mrow}
    ${worst === 4 ? `<div class="warn">시장 4단계: 와인스테인 원칙상 신규 매수 보류</div>` : worst === 3 ? `<div class="warn">시장 3단계: 매수 규모 축소, 손절 좁히기</div>` : ""}
  </section>
  ${breadthCard(SUM.breadth)}
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
function planLines(c) {
  const out = [];
  if (c.bkBuy) out.push(`<span>돌파 매수 <b>${fmt(c.bkBuy)}</b>~${fmt(c.bkMax)} · 손절 <b>${fmt(c.bkStop)}</b>${c.bkStatus === "추격 한도 넘음" ? " <span class='tag'>추격 금지</span>" : ""}</span>`);
  if (c.pbZone) out.push(`<span>풀백 매수 <b>${fmt(c.pbZone[0])}~${fmt(c.pbZone[1])}</b> · 손절 <b>${fmt(c.pbStop)}</b>${c.pbNow ? " <span class='tag good'>지금</span>" : ""}</span>`);
  return out.join("");
}
function candItem(c, rank) {
  return `<a class="item" href="#stock/${encodeURIComponent(c.t)}">
    <span class="row" style="gap:6px;flex-wrap:nowrap">${rank ? `<span class="rank">${rank}</span>` : ""}${ring(c.score, 42)}</span>
    <span style="min-width:0"><span class="t">${esc(c.t)}</span> ${c.signal ? `<span class="pill st2">${esc(c.kind || c.signal)}</span>` : ""}${c.mvOk ? ` <span class="pill st2" title="미너비니 트렌드 템플릿 충족">미너비니</span>` : ""}<div class="n">${capTag(c.cap, c.mc)}${esc(c.n)}${c.s ? " · " + esc(c.s) : ""}</div></span>
    <span class="r"><b>${fmt(c.close)}</b><span class="muted" style="font-size:11px">${pct(c.ext)} 이격</span></span>
    <span class="sub" style="display:grid;gap:2px">${planLines(c)}<span>거래량 <b>${c.volGrade}</b> · RS <b>${c.rsGrade}</b>등급${c.brkVr ? ` · 돌파 주 거래량 <b>${fmt(c.brkVr, 1)}배</b>` : ""}</span></span></a>`;
}

/* ---------- 화면: 후보 ---------- */
let candTab = "1→2 돌파", capTab = "대·중형";
function viewCands(v) {
  const buyTab = `매수 ${BUY_SCORE}+`;
  const tabs = [buyTab, "1→2 돌파", "돌파", "풀백", "베이스 대기", "65점+"];
  const inTab = (c, t) => t === buyTab ? !!c.signal && c.score >= BUY_SCORE : t === "65점+" ? c.score >= 65 : t === "1→2 돌파" ? c.kind === "1→2 돌파" : c.signal === t;
  /* 소형주(러셀2000)는 백테스트 성적이 나빠 기본 목록에서 빼고, '소형(참고)'·'내 종목'에서만 보여줌 */
  const capOk = c => capTab === "대·중형" ? c.cap !== "소형" : capTab === "소형(참고)" ? c.cap === "소형" : capTab === "내 종목" ? c.mine : c.cap === capTab;
  const list = SUM.candidates.filter(c => capOk(c) && inTab(c, candTab)).sort((a, b) => b.score - a.score);
  const desc = { "1→2 돌파": "1단계 기저(15주+)에서 막 2단계로 넘어가는 돌파 · 와인스테인의 최우선 매수 자리 (+10점)", "돌파": "최근 3주 안에 26주 고점을 거래량과 함께 종가 돌파", "풀백": "2단계 중 30주선 풀백(-3~+5%) 또는 돌파선 되돌림(±3%)", "베이스 대기": "1단계 베이스 상단 5% 이내, RS 개선 중", "65점+": "신호와 관계없이 주봉 점수 65점 이상", [buyTab]: `돌파·풀백 신호 + 점수 ${BUY_SCORE}점 이상 · 백테스트 매수 기준` }[candTab];
  v.innerHTML = `<h1>후보</h1>
    <div class="seg" role="group">${tabs.map(t => `<button data-ct="${t}" aria-pressed="${t === candTab}">${t} ${SUM.candidates.filter(c => capOk(c) && inTab(c, t)).length}</button>`).join("")}</div>
    <div class="seg" role="group">${["대·중형", "대형", "중형", "소형(참고)", "내 종목"].map(t => `<button data-cap="${t}" aria-pressed="${t === capTab}">${t}</button>`).join("")}</div>
    ${capTab === "소형(참고)" ? `<div class="warn">소형주는 백테스트에서 승률 6%·평균 손실로 성적이 나빴어요. 참고만 하고, 산다면 돌파 주 거래량이 평균 2배 이상일 때만 · 손절은 ATR×2(수량은 자동으로 줄어요).</div>` : ""}
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
    sg.innerHTML = hits.map(x => `<a class="item" href="#stock/${encodeURIComponent(x.t)}">${pill(x.st)}<span style="min-width:0"><span class="t">${esc(x.t)}</span><div class="n">${capTag(x.cap, x.mc)}${esc(x.n)}</div></span><span class="r"><b>${fmt(x.c)}</b>${sgn(x.w1)}</span></a>`).join("") || `<p class="note">찾는 종목이 없습니다. 목록에 없는 종목은 저장소의 my_tickers.txt에 추가하세요.</p>`;
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
  /* 추천 수량: 지금 신호의 매수가·손절 기준 (풀백이면 현재가와 풀백 손절, 아니면 돌파 매수가와 돌파 손절) */
  const usePb = td.signal === "풀백" && td.pbStop;
  const entry = usePb ? td.close : (td.bkBuy && td.close < td.bkBuy ? td.bkBuy : td.close);
  const stopFor = usePb ? td.pbStop : (td.bkStop || td.stop);
  const risk = num(stopFor) ? entry - stopFor : null;
  const acct = isKRW(t) ? s.account : s.account / fxRate();   // 계좌(원)를 종목 통화로
  const qtyRisk = risk > 0 ? Math.floor(acct * s.riskPct / 100 / risk) : null;
  const qtyCap = entry > 0 ? Math.floor(acct * 0.25 / entry) : null;      // 한 종목 최대 계좌 25%
  const capped = qtyRisk != null && qtyCap != null && qtyRisk > qtyCap;
  const qty = capped ? qtyCap : qtyRisk;
  const qtyAmt = qty ? Math.round(toKRW(qty * entry, t)) : null;
  const viol = td.score != null ? ruleCheck(td) : [];
  const bd = td.breakdown, parts = [["market", "시장", 10], ["sector", "업종", 20], ["stock", "진입 위치", 25], ["volume", "거래량", 25], ["rs", "RS", 10], ["risk", "손절폭", 10]];
  const held = store.get().holdings.find(h => h.t === t);
  v.innerHTML = `${searchBox()}
  <section class="card">
    <div class="row between" style="align-items:flex-start;flex-wrap:nowrap">
      <div style="min-width:0"><h1 class="mono">${esc(td.t)}</h1><div class="muted" style="font-size:13px">${esc(td.name)}${td.sector ? " · " + esc(td.sector) : ""}${td.mc ? " · 시총 " + mcTxt(td.mc) : ""}</div>
        <div class="row" style="margin-top:6px">${pill(td.stage, td.label)}${td.signal ? `<span class="pill st2">${esc(td.kind || td.signal)}</span>` : ""}</div></div>
      ${td.score != null ? ring(td.score, 76) : ""}
    </div>
    <div class="row"><b class="mono" style="font-size:22px">${fmt(last[W.c])}</b>${sgn(prev ? (last[W.c] / prev[W.c] - 1) * 100 : null)}<span class="muted" style="font-size:12px">주간 · ${last[W.d]} 주</span></div>
    <div class="seg" role="group">${[[52, "1년"], [104, "2년"], [156, "3년"]].map(([n, l]) => `<button data-wk="${n}" aria-pressed="${n === chartWeeks}">${l}</button>`).join("")}</div>
    <div class="row zoombar" role="group" aria-label="차트 확대·이동"><button class="btn sm" data-z="left" aria-label="과거로">◀</button><button class="btn sm" data-z="out" aria-label="축소">－</button><button class="btn sm" data-z="in" aria-label="확대">＋</button><button class="btn sm" data-z="right" aria-label="최근 쪽으로">▶</button><button class="btn sm" data-z="now">최근</button></div>
    <div><div class="chartinfo"></div><div class="chartbox" id="chart"></div></div>
    <p class="note"><span style="color:var(--ma)">━</span> 30주선 · 회색 막대 거래량 · 차트를 누르거나 끌면 그 주 값이 위에 나와요 · 두 손가락으로 벌리면 확대, 옆으로 끌면 이동 (PC는 마우스 휠)</p>
  </section>
  ${secMembers(t)}
  ${td.score != null ? `<section class="card"><h3>매수 계획</h3>
    <div class="kv">
      <div><span>돌파 매수가</span><b style="color:var(--s2)">${td.bkBuy ? fmt(td.bkBuy) : "–"}</b><small>${td.bkStatus ? esc(td.bkStatus) : ""}</small></div>
      <div><span>추격 한도</span><b>${td.bkMax ? fmt(td.bkMax) : "–"}</b><small>이 위면 사지 않기</small></div>
      <div><span>돌파 손절 (${td.small ? "ATR×2" : "-5%"})</span><b style="color:var(--s4)">${td.bkStop ? fmt(td.bkStop) : "–"}</b></div>
      <div><span>풀백 매수 구간</span><b style="color:var(--s2)">${td.pbZone ? fmt(td.pbZone[0]) + "~<wbr>" + fmt(td.pbZone[1]) : "–"}</b><small>${td.pbNow ? "지금 구간 안" : "30주선~+5%"}</small></div>
      <div><span>풀백 손절</span><b style="color:var(--s4)">${td.pbStop ? fmt(td.pbStop) : "–"}</b><small>${td.small ? "ATR×2까지 넓힘" : td.pbNow ? "3주 저점·30주선 아래" : "30주선 -2%"}</small></div>
      ${td.mhBuy ? `<div><span>잔고점 돌파가</span><b style="color:var(--s2)">${fmt(td.mhBuy)}</b><small>손절 ${fmt(td.mhStop)} · 전고점 ${fmt(td.mhMajor)} 앞에서 꺾이면 매도</small></div>` : ""}
      ${td.rtZone ? `<div><span>돌파 지점 되돌림</span><b style="color:var(--s2)">${fmt(td.rtZone[0])}~<wbr>${fmt(td.rtZone[1])}</b></div>` : ""}
      <div><span>30주선 이격</span><b style="${td.ext >= 20 ? "color:var(--s4)" : ""}">${pct(td.ext)}</b></div>
      <div><span>추천 수량 (${s.riskPct}% 위험)</span><b>${qty ? qty.toLocaleString() + "주" : "–"}</b><small>${qtyAmt ? "약 " + (qtyAmt / 10000).toFixed(0) + "만원" : ""}</small></div>
    </div>
    ${td.small ? `<div class="warn">소형주: 백테스트에서 성적이 나빴던 그룹이에요. 돌파는 그 주 거래량이 평균 2배 이상일 때만${td.brkVr ? ` (지금 ${fmt(td.brkVr, 1)}배)` : ""}, 손절은 주간 평균 변동폭(${fmt(td.atr)})×2로 넓히고 수량을 줄였어요.</div>` : ""}
    <p class="note">주문 요령 (백테스트 근거): 돌파 매수는 <b>스톱 지정가</b>로 · 지정가는 돌파 매수가 +2%${td.bkBuy ? ` (${fmt(td.bkBuy * 1.02)})` : ""}까지만 → 갭으로 그 위에서 시작하면 체결 안 됨 (갭 위 매수 0.96R vs 보통 1.93R). 손절은 금요일 종가가 손절가 아래면 매도 + 손절가 -3%${td.bkStop ? ` (${fmt(td.bkStop * 0.97)})` : ""}에 비상 자동 매도.</p>
    <p class="note">추천 수량: ${isKRW(t) ? "" : "환율 " + fmt(fxRate(), 0) + "원 기준, "}손절 시 계좌 ${Math.round(s.account / 10000).toLocaleString()}만원의 ${s.riskPct}%만 잃는 수량${capped ? " · 손절폭이 좁아 계좌의 25%로 줄였어요" : ""}</p>
    ${td.mvN != null ? `<p class="note">미너비니 트렌드 템플릿 <b style="color:${td.mvOk ? "var(--s2)" : "var(--muted)"}">${td.mvOk ? "충족" : "미충족"} (${td.mvN}/6)</b> · 30·40주선 위, 30주선 > 40주선, 40주선 상승, 52주 저점 +30% 이상, 52주 고점 -25% 이내. 참고용이에요 (백테스트: 충족 종목만 샀을 때 한 번은 연 +2%p, 다시 돌리면 차이 없음).</p>` : ""}
    ${viol.length ? `<div class="warn">규칙 점검: ${viol.map(esc).join(" · ")}</div>` : `<div class="ok">와인스테인 기본 조건 통과</div>`}
    <div class="btns">${held ? `<a class="btn" href="#hold">보유 중 · 손절 관리 보기</a>` : `<button class="btn primary" id="buyBtn">매수 기록</button>`}</div>
  </section>
  <section class="card"><h3>점수 구성</h3>
    <div class="bars">${parts.map(([k, l, m]) => { const val = bd?.[k], wd = val == null ? 0 : Math.min(1, val / m) * 100, c = wd >= 75 ? "var(--s2)" : wd >= 50 ? "#5f8a2c" : wd >= 25 ? "var(--s3)" : "var(--s4)"; return `<div class="brow"><span>${l}</span><div class="track"><div class="fill" style="width:${wd}%;background:${c}"></div></div><span class="v"><b>${val ?? "–"}</b>/${m}</span></div>`; }).join("")}
    ${lowWhy(td).length ? `<p class="note">낮은 항목: ${lowWhy(td).map(esc).join(" · ")}</p>` : ""}
    ${grade9("거래량", td.volGrade)}${grade9("RS", td.rsGrade)}</div>
    ${td.caps?.length ? `<p class="note">가감점·상한: ${td.caps.map(esc).join(", ")}</p>` : ""}
    <p class="note">자동 계산 점수입니다. 사기 전에 프로젝트에 차트를 보내 최종 확인하세요.</p>
  </section>` : `<section class="card"><p class="note">업종 ETF·지수는 단계만 표시합니다. 30주선 이격 ${pct(td.ext)}</p></section>`}`;
  bindSearch(v);
  const box = $("#chart", v);
  const view = { n: chartWeeks, end: null };
  const draw = () => { drawChart(box, td, view); v.querySelectorAll("[data-wk]").forEach(x => x.setAttribute("aria-pressed", view.end == null && Math.round(view.n) === +x.dataset.wk)); };
  draw();
  bindChartZoom(box, td, view, draw);
  v.querySelectorAll("[data-z]").forEach(b => b.onclick = () => { chartStep(td, view, b.dataset.z); draw(); });
  const ssb = $("#secsmall", v); if (ssb) ssb.onclick = () => { secCapAll = !secCapAll; route(); };
  v.querySelectorAll("[data-wk]").forEach(b => b.onclick = () => { chartWeeks = +b.dataset.wk; view.n = chartWeeks; view.end = null; draw(); });
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
  const defStop = td.signal === "돌파" ? (td.small && td.bkStop ? Math.min(td.bkStop, td.close - 2 * td.atr) : td.close * 0.95) : td.signal === "풀백" && td.pbStop ? td.pbStop : td.stop;
  const h = edit || { buyDate: today(), buyPrice: td.close, initStop: +(+defStop).toFixed(2), shares: qty || "" };
  openSheet(`<h2>${edit ? "보유 수정" : "매수 기록"} · <span class="mono">${esc(td.t)}</span></h2>
    ${viol?.length && !edit ? `<div class="warn">${td.score != null && td.score >= 60 && td.score < BUY_SCORE ? `<b>회색지대 (${Math.round(td.score)}점)</b> · ` : ""}규칙 점검: ${viol.map(esc).join(" · ")}<br>관심 종목에 두고 ${BUY_SCORE}점이 되면 사세요. 그래도 기록하면 일지에 '규칙 위반'으로 따로 집계돼요.</div>` : ""}
    <form class="form" id="bf">
      <div class="grid2"><label>매수일<input id="f_date" type="date" required value="${esc(h.buyDate)}"></label>
      <label>매수가<input id="f_price" type="number" step="any" inputmode="decimal" required value="${esc(h.buyPrice)}"></label></div>
      <div class="grid2"><label>초기 손절가<input id="f_stop" type="number" step="any" inputmode="decimal" required value="${esc(fmt(h.initStop).replace(/,/g, ""))}"></label>
      <label>수량<input id="f_sh" type="number" step="any" inputmode="decimal" required value="${esc(h.shares)}"></label></div>
      <label>메모<input id="f_note" type="text" maxlength="80" value="${esc(h.note || "")}" placeholder="예: 돌파 매수 절반"></label>
      <div class="note" id="f_risk"></div>
      ${viol?.length && !edit ? `<label class="note" style="display:flex;gap:8px;align-items:center"><input id="f_ack" type="checkbox"> 규칙 밖 매수인 걸 알고 기록해요</label>` : ""}
      <p class="note" id="f_err" style="color:var(--s4)"></p>
      <div class="btns"><button class="btn primary" type="submit">${edit ? "저장" : "보유에 추가"}</button><button class="btn" type="button" id="f_cancel">취소</button></div>
    </form>`, sh => {
    $("#f_cancel", sh).onclick = closeSheet;
    const st = store.get().settings;
    const riskInfo = () => {   // 손절까지 갔을 때 계좌 손실 (원·%)와 1% 규칙 수량
      const price = +$("#f_price", sh).value, stop = +$("#f_stop", sh).value, n = +$("#f_sh", sh).value;
      if (!(price > stop) || !(st.account > 0)) { $("#f_risk", sh).textContent = ""; return; }
      const loss = toKRW((price - stop) * (n || 0), td.t), lp = loss / st.account * 100, q = orderQty(td.t, price, stop, st);
      $("#f_risk", sh).innerHTML = `손절가까지 가면 <b class="${lp > st.riskPct ? "neg" : ""}">-${Math.round(loss).toLocaleString()}원 (계좌의 ${lp.toFixed(1)}%)</b>`
        + `${lp > st.riskPct ? ` · <b class="neg">${st.riskPct}% 규칙 초과</b>` : ""}<br>${st.riskPct}% 규칙 수량: ${q ?? "–"}주`;
    };
    ["#f_price", "#f_stop", "#f_sh"].forEach(k => $(k, sh).oninput = riskInfo);
    riskInfo();
    $("#bf", sh).onsubmit = e => {
      e.preventDefault();
      const ack = $("#f_ack", sh);
      if (ack && !ack.checked) { $("#f_err", sh).textContent = "규칙 밖 매수예요. 정말 기록하려면 체크하세요."; return; }
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
    <p class="note">백테스트에서 가장 나았던 규칙을 써요: 초기 손절(돌파 매수 -5%, 풀백 매수 = 3주 저점·30주선 아래)만 지키고, 그 뒤엔 종목이 4단계가 될 때까지 보유해요. 4단계가 되면 '4단계 · 매도'로 표시돼요.</p>
    <section class="card"><h3>종목 바로 추가</h3>
      <form class="form" id="qa">
        <div class="grid2"><label>티커<input id="qa_t" required autocapitalize="characters" placeholder="예: NVDA" style="text-transform:uppercase"></label>
        <label>매수가<input id="qa_p" type="number" step="any" inputmode="decimal" required></label></div>
        <div class="grid2"><label>수량<input id="qa_n" type="number" step="any" inputmode="decimal" required></label>
        <label>매수일<input id="qa_d" type="date" value="${today()}" required></label></div>
        <p class="note" id="qa_err" style="color:var(--s4)"></p>
        <button class="btn primary" type="submit">추가하고 평가</button>
      </form><p class="note">손절가는 그 종목의 지금 신호로 자동으로 정해져요(돌파 -5%, 풀백 = 3주 저점·30주선 아래, 그 외 매수가 -5%). 카드의 '수정'에서 바꿀 수 있어요.</p></section>
    <section class="card" id="pf"${H.length ? "" : " hidden"}><h3>포트폴리오 평가</h3><div class="muted">계산 중…</div></section>
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
  bindQuickAdd(v);
  if (H.length) renderPortfolio(v, H, d.settings);
}

function bindQuickAdd(v) {
  const f = $("#qa", v); if (!f) return;
  f.onsubmit = async e => {
    e.preventDefault();
    const t = $("#qa_t", v).value.trim().toUpperCase(), price = +$("#qa_p", v).value, n = +$("#qa_n", v).value, date = $("#qa_d", v).value;
    const err = $("#qa_err", v);
    if (!t || !(price > 0) || !(n > 0)) { err.textContent = "티커, 매수가, 수량을 확인하세요."; return; }
    let td = null; try { td = await loadT(t); } catch (_) { }
    if (!td) { err.textContent = `${t} 데이터가 없어요. S&P500·나스닥100·S&P400·러셀2000에 없는 종목은 저장소의 my_tickers.txt에 추가하면 다음 갱신부터 돼요.`; return; }
    const stop = td.signal === "풀백" && td.pbStop && td.pbStop < price ? td.pbStop : td.small && td.atr ? Math.min(price * 0.95, price - 2 * td.atr) : price * 0.95;
    const d = store.get();
    d.holdings.push({ id: uid(), t: td.t, buyDate: date, buyPrice: price, initStop: +stop.toFixed(2), shares: n, note: "", violations: ruleCheck(td), scoreAtBuy: td.score });
    store.save(); toast(`${td.t} 추가했어요`); route();
  };
}

/* 포트폴리오 평가: 종목 질 40 · 단계 20 · 섹터 분산 20 · 손절 위험 20 */
async function renderPortfolio(v, H, set) {
  const el = $("#pf", v); if (!el) return;
  const rows = [];
  for (const h of H) {
    try {
      const td = await loadT(h.t), st = holdStatus(td, h);
      const val = toKRW(st.close * h.shares, h.t), riskAmt = st.broke ? 0 : Math.max(0, toKRW((st.close - st.stop) * h.shares, h.t));
      rows.push({ h, td, st, val, riskAmt, sector: td.sector || "기타", score: td.score ?? null });
    } catch (_) { }
  }
  if (!document.body.contains(el) || !rows.length) return;
  const total = rows.reduce((a, r) => a + r.val, 0) || 1;
  const w = r => r.val / total;
  // 종목 질: 평가금액 가중 평균 점수
  const scored = rows.filter(r => r.score != null), sw = scored.reduce((a, r) => a + w(r), 0);
  const avgScore = sw ? scored.reduce((a, r) => a + r.score * w(r), 0) / sw : 0;
  const pQual = Math.round(avgScore * 0.4);
  // 단계: 2단계 비중
  const stageW = k => rows.filter(r => r.td.stage === k).reduce((a, r) => a + w(r), 0);
  const pStage = Math.round(20 * (stageW(2) + 0.5 * stageW(1) + 0.25 * stageW(3)));
  // 섹터 분산
  const secs = {}; rows.forEach(r => secs[r.sector] = (secs[r.sector] || 0) + w(r));
  const secList = Object.entries(secs).sort((a, b) => b[1] - a[1]);
  const top = secList[0][1] * 100;
  let pDiv = top <= 30 ? 20 : top <= 40 ? 15 : top <= 50 ? 10 : top <= 70 ? 5 : 0;
  if (rows.length >= 3 && secList.length === 1) pDiv = 0;
  // 손절 위험: 모든 종목이 손절될 때 계좌 손실 %
  const acct = set.account || total;
  const riskPct = rows.reduce((a, r) => a + r.riskAmt, 0) / acct * 100;
  const broken = rows.filter(r => r.st.broke || r.td.stage === 4).length;
  let pRisk = riskPct <= 5 ? 20 : riskPct <= 8 ? 15 : riskPct <= 12 ? 10 : riskPct <= 20 ? 5 : 0;
  pRisk = Math.max(0, pRisk - broken * 5);
  let score = pQual + pStage + pDiv + pRisk;
  const notes = [];
  if (SUM && SUM.worst === 4) { score = Math.min(score, 50); notes.push("시장 4단계: 신규 매수 보류, 보유 종목 손절 엄수"); }
  if (top > 40) notes.push(`${secList[0][0]} 비중 ${top.toFixed(0)}% · 한 업종 쏠림`);
  if (broken) notes.push(`손절 이탈·4단계 종목 ${broken}개 정리 필요`);
  if (riskPct > 12) notes.push(`전부 손절되면 계좌 -${riskPct.toFixed(1)}% · 손절 위험이 커요`);
  const weak = rows.filter(r => r.score != null && r.score < 50).map(r => r.h.t);
  if (weak.length) notes.push(`50점 미만: ${weak.join(", ")}`);
  if (rows.length > set.maxPos) notes.push(`최대 ${set.maxPos}종목 초과`);
  const bar = (lab, val, max) => { const p = Math.min(1, val / max) * 100, c = p >= 75 ? "var(--s2)" : p >= 50 ? "#5f8a2c" : p >= 25 ? "var(--s3)" : "var(--s4)"; return `<div class="brow"><span>${lab}</span><div class="track"><div class="fill" style="width:${p}%;background:${c}"></div></div><span class="v"><b>${val}</b>/${max}</span></div>`; };
  const palette = ["var(--s2)", "var(--s1)", "var(--s3)", "#8e5cc8", "#2a9db0", "var(--s4)", "var(--s0)"];
  el.innerHTML = `<h3>포트폴리오 평가</h3>
    <div class="row" style="flex-wrap:nowrap;gap:14px">${ring(Math.round(score), 76)}
      <div style="min-width:0"><b>${score >= 80 ? "튼튼해요" : score >= 65 ? "괜찮아요 · 보완점 있음" : score >= 50 ? "점검이 필요해요" : "위험해요"}</b>
      <div class="muted" style="font-size:12px">${rows.length}종목 · 평가금액 약 ${Math.round(total / 10000).toLocaleString()}만원 · 평균 점수 ${avgScore.toFixed(0)}</div></div></div>
    <div class="bars">${bar("종목 질", pQual, 40)}${bar("단계", pStage, 20)}${bar("섹터 분산", pDiv, 20)}${bar("손절 위험", pRisk, 20)}</div>
    <div><div style="display:flex;height:12px;border-radius:6px;overflow:hidden">${secList.map(([n, x], i) => `<span title="${esc(n)}" style="width:${x * 100}%;background:${palette[i % palette.length]}"></span>`).join("")}</div>
    <div class="row" style="gap:4px 12px;margin-top:6px;font-size:12px">${secList.map(([n, x], i) => `<span><span style="color:${palette[i % palette.length]}">●</span> ${esc(n)} ${(x * 100).toFixed(0)}%</span>`).join("")}</div></div>
    <p class="note">전부 손절되면 계좌 손실 약 ${riskPct.toFixed(1)}% (계좌 ${Math.round(acct / 10000).toLocaleString()}만원 기준)</p>
    ${notes.length ? `<div class="warn">${notes.map(esc).join("<br>")}</div>` : `<div class="ok">분산과 손절 위험 모두 양호해요</div>`}`;
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
      <div class="kv">${[["지킨 거래", good], ["어긴 거래", bad]].map(([lab, g]) => `<div><span>${lab} ${g.length}건 · 승률 ${g.length ? Math.round(g.filter(r => r.ret > 0).length / g.length * 100) : "–"}%</span><b>${g.length ? avg(g.map(r => r.R ?? 0)).toFixed(2) + "R · " : ""}<span class="${g.reduce((x, r) => x + r.pnl, 0) >= 0 ? "pos" : "neg"}">${Math.round(g.reduce((x, r) => x + r.pnl, 0)).toLocaleString()}원</span></b></div>`).join("")}</div>
      ${(() => { const bk = [["80점+", r => r.scoreAtBuy >= 80], ["회색지대 60~79점", r => r.scoreAtBuy >= 60 && r.scoreAtBuy < 80], ["60점 미만", r => r.scoreAtBuy < 60]].map(([lab, f]) => [lab, rows.filter(r => r.scoreAtBuy != null && f(r))]).filter(([, g]) => g.length);
        return bk.length ? `<div class="kv" style="margin-top:8px">${bk.map(([lab, g]) => `<div><span>${lab} ${g.length}건</span><b class="${g.reduce((x, r) => x + r.pnl, 0) >= 0 ? "pos" : "neg"}">${Math.round(g.reduce((x, r) => x + r.pnl, 0)).toLocaleString()}원</b></div>`).join("")}</div>` : ""; })()}
      <p class="note">매수할 때 점수 기준. 점수 없이 직접 추가한 거래는 빠져요.</p></section>
    <div class="list">${rows.map(r => `<div class="item"><span class="mono" style="font-weight:800">${esc(r.t)}</span>
      <span style="min-width:0"><span class="muted" style="font-size:12px">${esc(r.buyDate)} → ${esc(r.sellDate)} · ${esc(r.reason)}</span>${r.bad ? ` <span class="tag">규칙 위반</span>` : ""}</span>
      <span class="r"><b>${sgn(r.ret)}</b><span class="muted" style="font-size:11px">${r.R == null ? "" : r.R.toFixed(2) + "R"}</span></span>
      <span class="sub"><span>${fmt(r.buyPrice)} → ${fmt(r.sellPrice)} × ${r.shares}</span><span>SPY ${pct(r.spy)} · QQQ ${pct(r.qqq)}</span><button class="btn sm danger" data-deltr="${r.id}" style="padding:2px 8px">삭제</button></span></div>`).join("")}</div>`;
  v.querySelectorAll("[data-deltr]").forEach(b => b.onclick = () => confirmSheet("이 거래 기록을 삭제할까요?", () => { const d = store.get(); d.trades = d.trades.filter(x => x.id !== b.dataset.deltr); store.save(); route(); }));
}


/* ---------- 화면: 원칙·백테스트 정리 ---------- */
const GUIDE = [
  { h: "4단계 한눈에", open: true, items: [
    ["1단계 바닥", "30주선 평평, 주가가 그 위아래로 오르내림. 아직 사지 않고 관심종목에 둠"],
    ["2단계 상승", "주가가 30주선 위, 30주선이 올라감. 유일하게 사는 구간"],
    ["3단계 천장", "30주선이 평평해지고 자주 깨짐. 새로 사지 않음, 손절 좁히기"],
    ["4단계 하락", "주가가 30주선 아래, 30주선이 내려감. 보유 종목은 팔고 절대 사지 않음"]] },
  { h: "매수 원칙", items: [
    ["시장·업종 먼저", "시장(나스닥·S&P 중 나쁜 쪽)이 4단계면 신규 매수 없음, 업종 ETF도 2단계가 좋음"],
    ["돌파 매수", "1단계 박스(저항선)를 주봉 종가로 넘을 때, 돌파 주 거래량이 평소의 2배 이상이면 가장 좋음"],
    ["풀백 매수", "2단계에서 올라가는 30주선이나 돌파선까지 거래량이 줄며 내려왔을 때"],
    ["RS (맨스필드)", "0 위로 올라오는 중이거나 0 이상인 종목. 시장보다 약한 종목은 피함"],
    ["추격 금지", "돌파 지점보다 7% 넘게 올라가 있으면 사지 않고 되돌림을 기다림"]] },
  { h: "매도·손절 원칙", items: [
    ["손절 기준", "금요일 종가가 손절가 아래면 월요일에 매도, 장중에는 손절가 -3%(비상 손절)만 자동 매도"],
    ["4단계", "종목이 4단계로 들어가면 매도"],
    ["손절선 올리기", "수익이 나면 30주선·최근 저점 아래로 손절선을 따라 올림. 트레일링 스톱처럼 일찍 파는 규칙은 백테스트에서 모두 손해"],
    ["30주선 이탈", "실적 급락 뒤 30주선을 지킨 종목도 58%는 나중에 깨짐 → 30주선 이탈 매도 규칙이 핵심"]] },
  { h: "돈 관리", items: [
    ["1% 위험", "한 번 손절 시 계좌의 1%만 잃도록 수량 결정 (수량 = 계좌×1% ÷ (매수가-손절가))"],
    ["종목 수", "최대 6종목, 한 종목 최대 17%(= 100% ÷ 6)"],
    ["남는 돈은 QQQ", "현금으로 두면 연 11.4%, QQQ로 채우면 15.2%. 목표와 차이가 계좌의 25% 넘을 때만 맞추면 연 5회 매매"],
    ["QQQ도 4단계면 매도", "안 팔면 수익은 비슷한데 최대 낙폭이 -26% → -50%"],
    ["레버리지", "TQQQ 보유는 연 27%지만 최대 낙폭 -94%. 감당하기 어려워 쓰지 않음"]] },
  { h: "백테스트로 확인한 것 (2006년~, 대·중형주)", items: [
    ["전체 성과", "전략 연평균 약 15.4%, 최대 낙폭 약 -26% (SPY 그냥 보유 11.0%)"],
    ["1→2단계 돌파", "평균 1.61R vs 2단계 중간 돌파 1.37R → 1→2 돌파에 +10점"],
    ["기저 길이", "26주 이상 기저 돌파 1.54R vs 짧은 기저 1.10R → 긴 기저 +4, 짧은 기저 -8"],
    ["돌파 거래량", "2배 이상 2.87R vs 1배 미만 1.27R. 다만 거래량 부족하다고 바로 파는 규칙은 오히려 손해"],
    ["대박 종목 조합", "대·중형 + 기저 26주+ + 돌파 거래량 2배+ + RS 0 이상 → 평균 3.2R, 5건 중 1건은 3R 이상"],
    ["2단계 후반", "30주선이 40주 넘게 올랐거나 52주 +60% 이상이면 1.44R vs 1.62R → -8점"],
    ["돌파 실패 후 재돌파", "13주 안에 돌파 실패 뒤 다시 돌파하면 0.19R, 승률 11% → -20점"],
    ["풀백 전 30주선 이탈", "최근 13주 안에 30주선을 깬 뒤의 풀백 1.33R vs 1.72R → -10점"],
    ["헤드앤숄더 천장", "천장 패턴 완성 뒤 신호 0.78R vs 1.55R → -10점. 다른 차트 패턴(이중바닥, 역헤드앤숄더 등)은 효과 거의 없음"],
    ["바로 위 전고점 매물", "위 10% 안에 2년 내 전고점이 쌓이면 -8점 → 연평균 14.2 → 15.4%, 2008·2022 손실도 줄어듦 (큰 하락 뒤 긴 기저는 예외)"],
    ["소형주", "신호 성과가 크게 낮아 매수 신호에서 제외 (참고용으로만 표시)"],
    ["해외 ADR", "넣으면 연평균 15.4 → 12.1%로 낮아져 매수 신호 제외"],
    ["시장 약할 때", "방어 업종보다 경기민감 업종의 대·중형·RS 0 이상 종목이 대박 비율이 더 높음"]] },
  { h: "효과 없거나 나빴던 것", items: [
    ["60주선 터치 매수", "전체 평균은 30주선 터치보다 약함. RS 0 이상일 때만 좋음(3.5R), 30주선을 깬 상태면 평범"],
    ["30주선 회복 매수", "깨졌다 다시 올라선 주에 사면 대·중형 약 1.9R이지만 2016년 이후 약해짐"],
    ["횡보장 박스 매매", "박스 아래에서 사고 위에서 파는 방식, 횡보장 전환 규칙 모두 지금 전략보다 나쁨"],
    ["볼린저 수축 가점", "신호는 조금 좋아도 포트폴리오 수익 개선 없이 낙폭만 커져서 사용 안 함"],
    ["30주선 들락날락·급락 이력 감점", "효과가 없어 사용 안 함"],
    ["상승/하락 거래량 비율", "그것만으로는 차이 없음"],
    ["실적 급락 후 30주선 유지", "다른 신호와 성과 비슷, 특별히 좋지 않음"]] },
  { h: "꼭 알아둘 한계", items: [
    ["생존 편향", "지금 지수에 남아 있는 회사들로 과거를 돌려서 실제보다 좋게 나옴"],
    ["거래 수", "포트폴리오 실제 거래는 약 100건이라 1~2%p 차이는 우연일 수 있음"],
    ["이벤트 거래량", "인수합병·주식교환 때문에 늘어난 거래량은 수요가 아님 (SEC 공시 확인 기능은 SEC_UA 설정 후 작동)"],
    ["투자 조언 아님", "규칙을 기계적으로 적용한 결과이며 수익을 보장하지 않음"]] },
];
function viewGuide(v) {
  v.innerHTML = `<h1>원칙·백테스트 정리</h1>
  <p class="note">스탠 와인스타인의 단계 분석 원칙과, 이 앱의 점수·규칙을 정할 때 쓴 백테스트 결과를 모았어요. 숫자는 최근 백테스트 기준이며, 규칙이 바뀌면 함께 고쳐요.</p>
  ${GUIDE.map(g => `<details class="card guide"${g.open ? " open" : ""}><summary><h3>${esc(g.h)}</h3></summary>
    <div class="glist">${g.items.map(([k, d]) => `<div><b>${esc(k)}</b><span>${esc(d)}</span></div>`).join("")}</div></details>`).join("")}
  <div class="btns"><a class="btn" href="backtest/">전체 백테스트 결과 보기</a></div>`;
}

/* ---------- 화면: 설정·백업 ---------- */
function viewSettings(v) {
  const s = store.get().settings;
  v.innerHTML = `<h1>설정·백업</h1>
  <section class="card"><form class="form" id="setf">
    <label>계좌 금액 (원)<input id="g_acc" type="number" inputmode="numeric" value="${esc(s.account)}"></label>
    <div class="grid2"><label>한 번 손절 시 계좌 손실<select id="g_risk">${[0.5, 1, 1.5, 2].map(x => `<option value="${x}" ${+s.riskPct === x ? "selected" : ""}>${x}%</option>`).join("")}</select></label>
    <label>최대 보유 종목<input id="g_max" type="number" min="1" max="20" value="${esc(s.maxPos)}"></label></div>
    <label>QQQ 보유 수량 (남는 돈을 채우는 용도)<input id="g_qqq" type="number" inputmode="numeric" min="0" value="${esc(s.qqqShares || 0)}"></label>
    <button class="btn primary" type="submit">저장</button></form></section>
  <section class="card"><h3>백업</h3>
    <p class="note">보유 종목과 매매일지는 이 휴대폰 브라우저에만 저장돼요. 앱을 지우거나 브라우저 데이터를 지우면 사라지니 가끔 백업 파일을 받아 두세요.</p>
    <div class="btns"><button class="btn" id="exp">백업 파일 받기</button><label class="btn" for="imp">백업 불러오기</label><input id="imp" type="file" accept="application/json,.json" hidden></div></section>
  <section class="card"><h3>원칙·백테스트 정리</h3><div class="btns"><a class="btn" href="#guide">정리 보기</a></div></section>
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
  $("#setf", v).onsubmit = e => { e.preventDefault(); const d = store.get(); d.settings = { ...d.settings, account: +$("#g_acc", v).value || 0, riskPct: +$("#g_risk", v).value, maxPos: +$("#g_max", v).value || 6, qqqShares: +$("#g_qqq", v).value || 0 }; store.save(); toast("저장했어요"); };
  $("#exp", v).onclick = () => { const blob = new Blob([JSON.stringify(store.get(), null, 1)], { type: "application/json" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `stage-backup-${today()}.json`; document.body.appendChild(a); a.click(); a.remove(); };
  $("#imp", v).onchange = async e => { const f = e.target.files[0]; if (!f) return; try { const d = JSON.parse(await f.text()); if (!Array.isArray(d.holdings) || !Array.isArray(d.trades)) throw 0; store.replace(d); toast(`불러왔어요 · 보유 ${d.holdings.length} · 거래 ${d.trades.length}`); } catch (_) { toast("백업 파일 형식이 아니에요"); } };
}

/* ---------- 화면: 이번 주 주문 ---------- */
/* 백테스트 규칙 그대로: 매수는 신호 + 80점 이상(소형주 제외) · 빈자리만큼 점수 순 · 손절 시 계좌 riskPct% · 한 종목 최대 100/최대종목수%(25% 이하)
   매도는 4단계 진입 또는 금요일 종가가 손절가 아래 → 월요일 매도, 나머지는 손절 주문 유지 */
function orderQty(t, entry, stop, s) {
  const acct = isKRW(t) ? s.account : s.account / fxRate(), risk = entry - stop;
  if (!(risk > 0) || !(entry > 0)) return null;
  const capPct = Math.min(25, 100 / (s.maxPos || 6));
  return Math.max(0, Math.min(Math.floor(acct * s.riskPct / 100 / risk), Math.floor(acct * capPct / 100 / entry)));
}
async function weekOrders() {
  const d = store.get(), s = d.settings, H = d.holdings, sells = [], keeps = [];
  for (const h of H) {
    try {
      const td = await loadT(h.t), st = holdStatus(td, h);
      if (td.stage === 4) sells.push({ h, why: "4단계 진입", close: st.close });
      else if (st.close < st.stop) sells.push({ h, why: `금요일 종가 ${fmt(st.close)} < 손절 ${fmt(st.stop)}`, close: st.close });
      else keeps.push({ h, stop: st.stop, close: st.close });
    } catch (e) { keeps.push({ h, stop: +h.initStop, close: null, nodata: true }); }
  }
  const held = new Set(H.map(h => h.t));
  const slots = Math.max(0, (s.maxPos || 6) - (H.length - sells.length));
  const pool = SUM.worst === 4 ? [] : SUM.candidates.filter(c => c.signal && c.score >= BUY_SCORE && c.cap !== "소형" && !held.has(c.t)).sort((a, b) => b.score - a.score);
  const buys = [];
  for (const c of pool) {
    if (buys.length >= slots) break;
    let o = null;
    if (c.signal === "돌파" && c.bkBuy && c.bkStatus === "돌파 대기") {
      o = { kind: "스톱 지정가", stopPx: c.bkBuy, limit: Math.min(c.bkBuy * 1.02, c.bkMax || 1e12), entry: c.bkBuy, sl: c.bkStop, note: "가격이 스톱가에 닿으면 지정가로 매수 · 시가가 지정가 위면 체결 안 됨(갭 추격 금지)" };
    } else if (c.signal === "돌파" && c.bkBuy && c.bkStatus === "돌파함 · 매수 가능") {
      o = { kind: "지정가", limit: c.bkMax, entry: Math.min(c.close, c.bkMax), sl: c.bkStop, note: `이미 돌파함 · 월요일 ${fmt(c.bkMax)} 이하에서만 매수 (추격 한도)` };
    } else if (c.signal === "풀백" && c.pbNow && c.pbStop) {
      o = { kind: "지정가", limit: c.pbZone ? c.pbZone[1] : c.close, entry: c.close, sl: c.pbStop, note: `30주선 풀백 · 월요일 시가 근처 매수, ${fmt(c.pbZone ? c.pbZone[1] : c.close)} 위로 뛰면 보류` };
    }
    if (!o || !(o.entry > o.sl)) continue;
    o.qty = orderQty(c.t, o.entry, o.sl, s);
    if (!o.qty) continue;
    buys.push({ c, ...o, emerg: o.sl * 0.97, amt: Math.round(toKRW(o.qty * o.entry, c.t)) });
  }
  return { sells, keeps, buys, slots, more: pool.length - buys.length, s, q: await qqqPlan(s, keeps, buys) };
}
/* 남는 돈은 QQQ로: 목표(계좌 - 개별주, 시장 4단계면 0)와 차이가 계좌의 25% 넘을 때만 맞춤
   백테스트: 현금으로 두면 연 11.4% · QQQ 10%폭 15.4%(연 9회 매매) · 25%폭 15.2%(연 5회) · 4단계에도 안 팔면 낙폭 -50% */
const QQQ_BAND = 25;
async function qqqPlan(s, keeps, buys) {
  try {
    const qd = await loadT("QQQ"), px = qd.w[qd.w.length - 1][W.c], pxK = toKRW(px, "QQQ");
    const held = +s.qqqShares || 0;
    const stk = keeps.reduce((a, k) => a + (k.close ? toKRW(k.close * +k.h.shares, k.h.t) : 0), 0) + buys.reduce((a, b) => a + b.amt, 0);
    const cur = held * pxK, target = SUM.worst === 4 ? 0 : Math.max(0, (+s.account || 0) - stk);
    const diff = target - cur;
    let sh = 0;
    if (target === 0 && held > 0) sh = -held;
    else if (Math.abs(diff) > (+s.account || 0) * QQQ_BAND / 100) sh = diff > 0 ? Math.floor(diff / pxK) : -Math.min(held, Math.ceil(-diff / pxK));
    return { px, held, cur, target, sh, stk };
  } catch (e) { return null; }
}
/* 키움 자동감시주문(서버 감시)에 옮겨 넣을 값: 보유·신규 매수 종목의 비상 손절(스탑로스), 돌파 대기 종목의 매수 감시 */
const WATCH_DAYS = 30;     // 미국 주식 자동감시주문 유효기간 (키움 화면에서 실제 값 확인)
function watchRows(O) {
  const sl = O.keeps.map(k => ({ t: k.h.t, side: "매도", trig: k.stop * 0.97, cond: "이하", qty: +k.h.shares, price: "시장가 (안 되면 감시가격 -1% 지정가)" }));
  const by = O.buys.filter(b => b.stopPx).map(b => ({ t: b.c.t, side: "매수", trig: b.stopPx, cond: "이상", qty: b.qty, price: `지정가 ${fmt(b.limit)}` }));
  const bsl = O.buys.map(b => ({ t: b.c.t, side: "매도", trig: b.emerg, cond: "이하", qty: b.qty, price: "시장가 (안 되면 감시가격 -1% 지정가)", after: true }));
  return { sl, by, bsl };
}
function watchAge(s) {
  if (!s.watchSetAt) return null;
  return Math.floor((Date.now() - new Date(s.watchSetAt + "T00:00:00").getTime()) / 86400000);
}
function ordersText(O) {
  const L = [`[와인스테인 이번 주 주문] ${SUM.updated ? new Date(SUM.updated).toLocaleDateString("ko-KR") : ""} 데이터`];
  if (O.sells.length) { L.push("", "■ 매도 (월요일 장 시작)"); O.sells.forEach(x => L.push(`- ${x.h.t} ${x.h.shares}주 매도 · ${x.why}`)); }
  if (O.buys.length) {
    L.push("", "■ 매수");
    O.buys.forEach(b => L.push(`- ${b.c.t} (${b.c.kind || b.c.signal} · ${b.c.score}점) ${b.stopPx ? `스톱 ${fmt(b.stopPx)} · 지정가` : "지정가"} ${fmt(b.limit)} · ${b.qty}주`,
      `  손절: 금요일 종가 ${fmt(b.sl)} 아래면 매도 · 비상 자동 매도 ${fmt(b.emerg)}`));
  }
  if (O.keeps.length) { L.push("", "■ 보유 유지 (손절 주문 확인)"); O.keeps.forEach(k => L.push(`- ${k.h.t}: 금요일 종가 ${fmt(k.stop)} 아래면 매도 · 비상 ${fmt(k.stop * 0.97)}`)); }
  if (O.q && O.q.sh) L.push("", `■ QQQ (남는 돈)`, `- QQQ ${O.q.sh > 0 ? "매수" : "매도"} ${Math.abs(O.q.sh)}주 (월요일 시가 근처)`);
  if (!O.sells.length && !O.buys.length && !(O.q && O.q.sh)) L.push("", "이번 주 새 주문 없음");
  const R = watchRows(O), line = r => `- ${r.t} ${r.side}: 현재가 ${fmt(r.trig)} ${r.cond} → ${r.price} · ${r.qty}주${r.after ? " (매수 체결 뒤)" : ""}`;
  if (R.sl.length || R.by.length || R.bsl.length) {
    L.push("", `■ 키움 자동감시주문 (유효 ${WATCH_DAYS}일)`);
    [...R.sl, ...R.by, ...R.bsl].forEach(r => L.push(line(r)));
  }
  return L.join("\n");
}
async function viewOrders(v) {
  v.innerHTML = `<h1>이번 주 주문</h1><div class="muted">계산 중…</div>`;
  const O = await weekOrders();
  if (!document.body.contains(v)) return;
  const s = O.s;
  v.innerHTML = `<h1>이번 주 주문</h1>
  ${SUM.weekComplete ? "" : `<div class="warn">이번 주봉이 아직 확정되지 않았어요(주중 데이터). 주문은 금요일 미국장 마감 뒤, 토요일에 확정해서 넣으세요.</div>`}
  ${SUM.worst === 4 ? `<div class="warn">시장 4단계: 신규 매수 없음 (매도·손절만)</div>` : ""}
  <section class="card"><h3>매도 ${O.sells.length}건</h3>
    ${O.sells.length ? `<div class="list">${O.sells.map(x => `<a class="item" href="#stock/${encodeURIComponent(x.h.t)}"><span class="pill st4">매도</span><span style="min-width:0"><b class="mono">${esc(x.h.t)}</b> ${x.h.shares}주<div class="n">${esc(x.why)} · 월요일 장 시작에 매도</div></span><span class="r"><b>${fmt(x.close)}</b></span></a>`).join("")}</div>` : `<p class="note">팔 종목이 없어요.</p>`}
  </section>
  <section class="card"><h3>매수 ${O.buys.length}건 <span class="muted" style="font-size:12px;font-weight:400">빈자리 ${O.slots} · ${BUY_SCORE}점+ 신호 · 소형주 제외</span></h3>
    ${O.buys.length ? `<div class="list">${O.buys.map(b => `<a class="item" href="#stock/${encodeURIComponent(b.c.t)}">${ring(b.c.score, 42)}
      <span style="min-width:0"><b class="mono">${esc(b.c.t)}</b> <span class="pill st2">${esc(b.c.kind || b.c.signal)}</span>${b.c.mvOk ? ` <span class="pill st2">미너비니</span>` : ""}
        <div class="n">${b.stopPx ? `스톱 지정가 · 스톱 <b>${fmt(b.stopPx)}</b> · 지정가` : "지정가"} <b>${fmt(b.limit)}</b> · <b>${b.qty}주</b> (약 ${(b.amt / 10000).toFixed(0)}만원)</div>
        <div class="n">손절: 금요일 종가 <b style="color:var(--s4)">${fmt(b.sl)}</b> 아래면 매도 · 비상 자동 매도 <b style="color:var(--s4)">${fmt(b.emerg)}</b></div>
        <div class="n muted">${esc(b.note)}</div></span><span></span></a>`).join("")}</div>`
      : `<p class="note">${O.slots ? "조건에 맞는 매수 신호가 없어요." : `빈자리가 없어요 (최대 ${s.maxPos}종목).`}</p>`}
    ${O.more > 0 && O.buys.length ? `<p class="note">자리가 없어 빠진 ${BUY_SCORE}점+ 신호 ${O.more}개는 '후보 → 매수 ${BUY_SCORE}+'에서 볼 수 있어요.</p>` : ""}
  </section>
  ${O.q ? `<section class="card"><h3>QQQ (남는 돈) ${O.q.sh ? `<span class="pill ${O.q.sh > 0 ? "st2" : "st4"}">${O.q.sh > 0 ? "매수" : "매도"} ${Math.abs(O.q.sh)}주</span>` : `<span class="pill">그대로</span>`}</h3>
    <div class="n">보유 ${O.q.held}주 (약 ${Math.round(O.q.cur / 10000).toLocaleString()}만원) · 목표 약 ${Math.round(O.q.target / 10000).toLocaleString()}만원 · 현재가 ${fmt(O.q.px)}</div>
    <p class="note">${SUM.worst === 4 ? "시장 4단계라 QQQ는 모두 팔아요." : `계좌에서 개별주(약 ${Math.round(O.q.stk / 10000).toLocaleString()}만원)를 뺀 나머지를 QQQ로 채워요. 목표와 차이가 계좌의 ${QQQ_BAND}%를 넘을 때만 사고팔아서 1년에 5번 정도만 손대면 돼요.`}
    QQQ 보유 수량은 설정에서 입력해요. 개별주를 살 돈이 모자라면 QQQ를 팔아서 마련하세요.</p></section>` : ""}
  ${O.keeps.length ? `<section class="card"><h3>보유 유지 ${O.keeps.length}종목</h3><p class="note">증권사에 걸어 둔 손절 주문이 맞는지 확인하세요.</p>
    <div class="list">${O.keeps.map(k => `<a class="item" href="#stock/${encodeURIComponent(k.h.t)}"><span class="pill st2">유지</span><span style="min-width:0"><b class="mono">${esc(k.h.t)}</b> ${k.h.shares}주<div class="n">금요일 종가 ${fmt(k.stop)} 아래면 매도 · 비상 자동 매도 ${fmt(k.stop * 0.97)}</div></span><span class="r"><b>${fmt(k.close)}</b></span></a>`).join("")}</div></section>` : ""}
  ${(() => {
    const R = watchRows(O), age = watchAge(s);
    if (!R.sl.length && !R.by.length && !R.bsl.length) return "";
    const row = r => `<div class="item"><span class="pill ${r.side === "매수" ? "st2" : "st4"}">${r.side}</span><span style="min-width:0"><b class="mono">${esc(r.t)}</b> ${r.qty}주${r.after ? ` <span class="tag">매수 체결 뒤</span>` : ""}
      <div class="n">감시 조건: 현재가 <b>${fmt(r.trig)}</b> ${r.cond}</div><div class="n">주문: ${esc(r.price)}</div></span><span></span></div>`;
    return `<section class="card"><h3>키움 자동감시주문 설정값</h3>
      <p class="note">키움 서버가 대신 가격을 감시해요(휴대폰이 꺼져 있어도 됨). 영웅문S# → 해외주식 → 자동감시주문에서 아래 값을 그대로 넣으세요.</p>
      ${age == null ? `<div class="warn">설정한 날짜가 기록돼 있지 않아요. 키움에 넣은 뒤 아래 '오늘 설정함'을 눌러 두면 만료 전에 알려 드려요.</div>`
        : age >= WATCH_DAYS - 5 ? `<div class="warn">마지막 설정 ${age}일 전 · 유효기간(${WATCH_DAYS}일)이 곧 끝나요. 다시 설정하세요.</div>`
        : `<p class="note">마지막 설정 ${age}일 전 (${esc(s.watchSetAt)}) · ${WATCH_DAYS - age}일 남음</p>`}
      ${R.sl.length ? `<h4>보유 종목 비상 손절 (스탑로스)</h4><div class="list">${R.sl.map(row).join("")}</div>` : ""}
      ${R.by.length ? `<h4>돌파 매수 감시</h4><p class="note">미국 주식에서 '매수' 감시가 되는지 키움 화면에서 확인하세요. 안 되면 위 매수 목록대로 스톱 지정가나 직접 주문으로 넣으세요.</p><div class="list">${R.by.map(row).join("")}</div>` : ""}
      ${R.bsl.length ? `<h4>새로 산 종목 비상 손절</h4><div class="list">${R.bsl.map(row).join("")}</div>` : ""}
      <p class="note">금요일 종가 손절과 4단계 매도는 가격 감시로 걸 수 없어요. 토요일에 이 화면의 '매도'를 보고 월요일에 직접 파세요. 트레일링 스탑은 쓰지 마세요(백테스트에서 일찍 파는 규칙은 모두 손해).</p>
      <div class="btns"><button class="btn sm" id="wset">오늘 설정함</button></div></section>`;
  })()}
  <section class="card"><div class="btns"><button class="btn primary" id="oshare">주문 목록 보내기·복사</button></div>
    <p class="note">수량은 계좌 ${Math.round(s.account / 10000).toLocaleString()}만원 · 손절 시 ${s.riskPct}% 손실 · 한 종목 최대 ${Math.min(25, 100 / (s.maxPos || 6)).toFixed(0)}% 기준이에요(설정에서 바꿀 수 있어요). 자동 계산 결과라 주문 전에 차트를 한 번 더 확인하세요. 투자 조언이 아니며 수익을 보장하지 않습니다.</p></section>`;
  const ws = $("#wset", v); if (ws) ws.onclick = () => { const d = store.get(); d.settings.watchSetAt = today(); store.save(); toast("설정 날짜를 기록했어요"); viewOrders(v); };
  $("#oshare", v).onclick = async () => {
    const text = ordersText(O);
    try { if (navigator.share) { await navigator.share({ title: "이번 주 주문", text }); return; } } catch (e) { if (e && e.name === "AbortError") return; }
    try { await navigator.clipboard.writeText(text); toast("주문 목록을 복사했어요"); } catch (e) { toast("복사하지 못했어요"); }
  };
}

/* ---------- 라우터 ---------- */
async function route() {
  const v = $("#view");
  const [tab, arg] = (location.hash.slice(1) || "home").split("/");
  document.querySelectorAll(".tabs a").forEach(a => a.toggleAttribute("aria-current", false));
  const cur = document.querySelector(`.tabs a[data-tab="${tab === "settings" ? "hold" : tab === "guide" ? "home" : tab}"]`); if (cur) cur.setAttribute("aria-current", "page");
  if (!SUM) { try { await loadSummary(); } catch (e) { v.innerHTML = `<div class="empty">데이터를 불러오지 못했어요.<br>아직 첫 갱신 전이거나 인터넷 연결이 끊겼어요.</div>`; $("#updated").textContent = "데이터 없음"; return; } }
  window.onresize = null;
  try {
    if (tab === "cands") viewCands(v);
    else if (tab === "orders") await viewOrders(v);
    else if (tab === "stock") await viewStock(v, arg ? decodeURIComponent(arg).toUpperCase() : "");
    else if (tab === "hold") await viewHold(v);
    else if (tab === "journal") await viewJournal(v);
    else if (tab === "settings") viewSettings(v);
    else if (tab === "guide") viewGuide(v);
    else await viewHome(v);
  } catch (e) { console.error(e); v.innerHTML = `<div class="empty">화면을 그리다 문제가 생겼어요. 새로고침해 보세요.</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => { });

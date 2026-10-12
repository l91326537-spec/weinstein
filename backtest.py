# -*- coding: utf-8 -*-
"""
와인스테인 점수 백테스트
- 스크리너와 똑같은 계산(screener.py)을 과거 매주에 적용합니다. 각 주는 그 주까지의 데이터만 씁니다.
- 매수: 그 주 신호(돌파/풀백) + 점수 기준 이상 → 다음 주 시가에 매수
- 수량: 손절 시 계좌의 1%만 잃도록 (한 종목 최대 25%, 동시 보유 최대 10종목)
- 손절: 초기 손절(최근 10주 저점 아래) → 수익이 초기 손절폭만큼 나거나 3주 이상 수익이면 본전
        → 이후 30주선·최근 8주 저점 중 높은 쪽 아래로 추적 (주중 저가가 닿으면 매도)
        → 종목이 4단계로 바뀌면 다음 주 시가에 매도
- 비교: SPY, QQQ, 같은 종목들을 똑같이 나눠 들고 있었을 때(동일가중)
실행: python backtest.py                (기본: 2006년부터, 점수 70 이상)
      python backtest.py --quick        (나스닥100만, 빠른 확인)
      python backtest.py --start 2010-01-01 --min-score 80
"""
import argparse, datetime as dt, html, math, os, sys

import numpy as np
import pandas as pd

import screener as S

BT = {
    "risk_pct": 1.0,      # 한 번 손절 시 계좌 손실 %
    "max_pos": 10,        # 동시 보유 최대 종목 수
    "max_weight": 25.0,   # 한 종목 최대 비중 %
    "max_stop_pct": 20.0, # 초기 손절폭이 이보다 크면 매수 안 함
    "min_stop_pct": 3.0,  # 손절이 이보다 가까우면 이만큼 아래로 (너무 촘촘한 손절로 수량이 과해지는 것 방지)
    "cost_pct": 0.1,      # 매수·매도 각각 수수료+슬리피지 %
    "be_weeks": 3,        # 이 주 수 이상 수익이면 손절을 본전으로
    "signals": ("돌파", "풀백"),
    "no_sector_pts": 12,  # 섹터 ETF가 아직 없던 기간(XLC 2018년 이전 등)은 업종 점수를 중립값으로
}


# ---------------- 신호 만들기 ----------------
def build_frames(dfs, uni):
    bench = dfs["^GSPC"]["Close"]
    print("지표 계산 중...")
    # 시장 단계 (나스닥·S&P 중 나쁜 쪽)
    order = {4: 3, 3: 2, 1: 1, 2: 0}
    mstages = []
    for t in S.MARKET:
        if t in dfs:
            f = S.indicators(dfs[t])
            mstages.append(f.apply(lambda r: (S.classify(r) or (None,))[0], axis=1).rename(t))
    mk = pd.concat(mstages, axis=1).reindex(bench.index, method="ffill")
    market = mk.apply(lambda r: max([x for x in r if pd.notna(x)] or [np.nan], key=lambda s: order.get(s, -1) if pd.notna(s) else -1), axis=1)
    # 섹터 ETF 단계·RS
    sec = {}
    for name, etf in S.SECTOR_ETF.items():
        if etf in dfs:
            f = S.indicators(dfs[etf])
            st = f.apply(lambda r: (S.classify(r) or (None,))[0], axis=1)
            rs = S.mansfield_series(f["close"], bench)
            sec[name] = pd.DataFrame({"stage": st, "rs": rs})
    frames = {}
    for i, (t, info) in enumerate(uni.items()):
        if t not in dfs or info.get("bench") != "us":
            continue
        f = S.indicators(dfs[t])
        f["rs"] = S.mansfield_series(f["close"], bench)
        f["rs4"] = f["rs"].shift(4)
        cls = f.apply(S.classify, axis=1)
        f["stage"] = cls.map(lambda x: x[0] if x else np.nan)
        f["stage_label"] = cls.map(lambda x: x[1] if x else None)
        f["mkt"] = market.reindex(f.index, method="ffill")
        s = sec.get(info.get("sector"))
        if s is not None:
            s2 = s.reindex(f.index, method="ffill")
            f["sec_stage"], f["sec_rs"] = s2["stage"], s2["rs"]
        else:
            f["sec_stage"], f["sec_rs"] = np.nan, np.nan
        scores, sigs = [], []
        for _, r in f.iterrows():
            if pd.isna(r["stage"]) or pd.isna(r["rs"]):
                scores.append(np.nan); sigs.append(""); continue
            d = S.row_dict(r)
            d["stage"] = int(r["stage"])
            ss = None if pd.isna(r["sec_stage"]) else int(r["sec_stage"])
            ms = None if pd.isna(r["mkt"]) else int(r["mkt"])
            sc, bd, _ = S.score_stock(d, ms, ss, None if pd.isna(r["sec_rs"]) else r["sec_rs"])
            if ss is None:
                sc = min(100, sc + BT["no_sector_pts"])
            ok = (r["close"] >= S.CFG["min_price"]) and (r["dollar_vol"] >= S.CFG["min_dollar_vol"])
            scores.append(sc if ok else np.nan)
            sigs.append(S.signal_of(d) if ok else "")
        f["score"], f["signal"] = scores, sigs
        frames[t] = f
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(uni)}")
    return frames, market


# ---------------- 거래 한 건 시뮬레이션 ----------------
def simulate_trade(f, i):
    """i주 신호 → i+1주 시가 매수. (진입일, 청산일, 진입가, 청산가, R배수, 보유주, 청산사유, 초기손절) 또는 None"""
    if i + 1 >= len(f):
        return None
    o, hi, lo, c = f["open"].values, f["high"].values, f["low"].values, f["close"].values
    ma, low8, stg = f["ma30"].values, f["low8"].values, f["stage"].values
    e = o[i + 1]
    s = f["stop"].values[i]
    if e > 0 and s > e * (1 - BT["min_stop_pct"] / 100):
        s = e * (1 - BT["min_stop_pct"] / 100)
    if not (e > s > 0) or (e - s) / e * 100 > BT["max_stop_pct"]:
        return None
    risk = e - s
    s0 = s
    be = False
    j = i + 1
    while j < len(f):
        if lo[j] <= s:                                  # 손절 (갭 하락이면 시가)
            px = min(s, o[j])
            return (f.index[i + 1], f.index[j], e, px, (px - e) / risk, j - i - 1, "손절" if px < e else "추적 손절", s0)
        held = j - i
        if not be and (c[j] >= e + risk or (held >= BT["be_weeks"] and c[j] > e)):
            be = True
            s = max(s, e * (1 + 2 * BT["cost_pct"] / 100))
        if be:
            trail = max(ma[j] if not math.isnan(ma[j]) else 0, low8[j] if not math.isnan(low8[j]) else 0) * 0.99
            s = max(s, trail)
        if stg[j] == 4 and j + 1 < len(f):           # 4단계 → 다음 주 시가 매도
            px = o[j + 1]
            return (f.index[i + 1], f.index[j + 1], e, px, (px - e) / risk, j - i, "4단계 진입", s0)
        j += 1
    return (f.index[i + 1], None, e, c[-1], (c[-1] - e) / risk, len(f) - i - 2, "보유 중", s0)


def collect_signals(frames, start, min_score_floor=50):
    rows = []
    for t, f in frames.items():
        idx = np.where((f["signal"].isin(BT["signals"])) & (f["score"] >= min_score_floor) & (f.index >= start))[0]
        last = -99
        for i in idx:
            dedupe = i - last <= 4                      # 4주 안에 반복된 신호는 같은 셋업으로 보고 통계에서 제외
            last = i
            tr = simulate_trade(f, i)
            if tr is None:
                continue
            ent, ex, e, px, R, wk, why, s0 = tr
            rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px,
                             stop0=s0,
                             R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why,
                             score=float(f["score"].iloc[i]), signal=f["signal"].iloc[i],
                             stage_label=f["stage_label"].iloc[i], dup=dedupe))
    return pd.DataFrame(rows)


# ---------------- 포트폴리오 ----------------
def run_portfolio(sig, frames, cal, min_score):
    cands = sig[(sig["score"] >= min_score)].sort_values(["entry_date", "score"], ascending=[True, False])
    by_week = {d: g for d, g in cands.groupby("entry_date")}
    cash, equity = 100.0, 100.0
    pos = {}       # ticker -> dict(shares, exit_date, exit, frame)
    curve, trades = [], []
    cost = BT["cost_pct"] / 100
    for d in cal:
        # 1) 이번 주 시가 매수 (지난주까지의 자금으로)
        if d in by_week:
            for _, r in by_week[d].iterrows():
                if len(pos) >= BT["max_pos"] or r["ticker"] in pos:
                    continue
                risk_ps = r["entry"] - r["stop0"]
                if risk_ps <= 0:
                    continue
                shares = equity * BT["risk_pct"] / 100 / risk_ps
                val = min(shares * r["entry"], equity * BT["max_weight"] / 100, cash / (1 + cost))
                if val < equity * 0.01:
                    continue
                shares = val / r["entry"]
                cash -= val * (1 + cost)
                pos[r["ticker"]] = dict(shares=shares, exit_date=r["exit_date"], exit=r["exit"], r=r)
        # 2) 이번 주 청산
        for t in list(pos):
            p = pos[t]
            if p["exit_date"] is not None and p["exit_date"] <= d:
                cash += p["shares"] * p["exit"] * (1 - cost)
                trades.append(p["r"])
                del pos[t]
        # 3) 주말 평가
        mv = 0.0
        for t, p in pos.items():
            cl = frames[t]["close"]
            px = cl.asof(d)
            mv += p["shares"] * (px if pd.notna(px) else p["r"]["entry"])
        equity = cash + mv
        curve.append((d, equity, len(pos)))
    eq = pd.DataFrame(curve, columns=["date", "equity", "n"]).set_index("date")
    return eq, pd.DataFrame(trades)


def stats_curve(eq):
    e = eq.dropna()
    if len(e) < 10:
        return {}
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    wk = e.pct_change().dropna()
    vol = wk.std() * math.sqrt(52)
    return dict(total=(e.iloc[-1] / e.iloc[0] - 1) * 100, cagr=cagr * 100, mdd=dd * 100,
                vol=vol * 100, mar=(cagr / abs(dd)) if dd < 0 else np.nan)


def stats_trades(t):
    if t is None or len(t) == 0:
        return dict(n=0)
    w = t[t["R"] > 0]; l = t[t["R"] <= 0]
    pf = w["R"].sum() / abs(l["R"].sum()) if len(l) and l["R"].sum() != 0 else np.nan
    return dict(n=len(t), win=len(w) / len(t) * 100, avgR=t["R"].mean(), medR=t["R"].median(),
                avgW=w["R"].mean() if len(w) else np.nan, avgL=l["R"].mean() if len(l) else np.nan,
                pf=pf, weeks=t["weeks"].mean(), ret=t["ret"].mean(), big=(t["R"] >= 3).mean() * 100)


# ---------------- 리포트 ----------------
def f_(x, d=1, pct=False):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "–"
    return f"{x:,.{d}f}{'%' if pct else ''}"


def line_svg(series, w=1000, h=320):
    cols = ["#1d7a4b", "#4f6d84", "#b0741a", "#8a8f8c"]
    df = pd.concat(series.values(), axis=1, keys=series.keys()).dropna()
    if df.empty:
        return ""
    lg = np.log(df)
    lo, hi = lg.min().min(), lg.max().max()
    pad = 44
    xs = np.linspace(pad, w - 10, len(df))
    def y(v): return h - 24 - (v - lo) / (hi - lo) * (h - 40)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="자산 곡선 (로그 축)">']
    for k in [1, 2, 5, 10, 20, 50, 100]:
        v = math.log(100 * k)
        if lo <= v <= hi:
            parts.append(f'<line x1="{pad}" x2="{w-10}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="currentColor" opacity=".12"/>'
                         f'<text x="{pad-6}" y="{y(v)+4:.1f}" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">{k}배</text>')
    years = sorted({d.year for d in df.index})
    for yr in years[::max(1, len(years) // 10)]:
        i = np.searchsorted(df.index, pd.Timestamp(f"{yr}-01-01"))
        if i < len(xs):
            parts.append(f'<text x="{xs[i]:.1f}" y="{h-6}" font-size="11" text-anchor="middle" fill="currentColor" opacity=".6">{yr}</text>')
    for n, (k, col) in enumerate(zip(df.columns, cols)):
        pts = " ".join(f"{x:.1f},{y(v):.1f}" for x, v in zip(xs, lg[k]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{2.2 if n==0 else 1.4}"/>')
    parts.append("</svg>")
    leg = " ".join(f'<span style="color:{c}">━ {html.escape(k)}</span>' for k, c in zip(df.columns, cols))
    return "".join(parts) + f'<div class="leg">{leg}</div>'


def write_report(path, args, curves, cstats, tstats, variants, buckets, sigtab, yearly, sig, survivors_note):
    e = html.escape
    css = """
    :root{--bg:#f4f6f5;--card:#fff;--fg:#17201c;--mut:#5b6862;--line:#d8dfdb;--g:#1d7a4b;--r:#b13a3a}
    @media(prefers-color-scheme:dark){:root{--bg:#111614;--card:#19211d;--fg:#e4ebe7;--mut:#93a19a;--line:#2a3530;--g:#5fcf93;--r:#ef7f7f}}
    body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;margin:0}
    .w{max-width:1050px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:30px 0 8px}
    .mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
    .tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
    table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
    th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
    th{font-size:12px;color:var(--mut);font-weight:600}td.l,th.l{text-align:left}tr:last-child td{border-bottom:0}
    .pos{color:var(--g)}.neg{color:var(--r)}.leg{font-size:12px;display:flex;gap:16px;flex-wrap:wrap;margin-top:4px}
    .warn{border-left:4px solid var(--r)}b.big{font-size:20px}
    .kp{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}.kp div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
    .kp>div>span{display:block;font-size:12px;color:var(--mut)}
    """
    def sgn(x, d=1, pct=True):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "–"
        return f'<span class="{"pos" if x > 0 else "neg" if x < 0 else ""}">{f_(x, d, pct)}</span>'
    P = [f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>와인스테인 백테스트</title><style>{css}</style><div class=w>",
         f"<h1>와인스테인 백테스트</h1><div class=mut>{e(args.start)} ~ {dt.date.today()} · 점수 {args.min_score}점 이상 · "
         f"신호 {', '.join(BT['signals'])} · 1회 손절 시 계좌 {BT['risk_pct']}% · 최대 {BT['max_pos']}종목 · 비용 편도 {BT['cost_pct']}%</div>"]
    st = cstats["전략"]; tt = tstats
    P.append("<h2>한눈에</h2><div class=kp>"
             f"<div><span>연평균 수익률</span><b class=big>{sgn(st.get('cagr'))}</b></div>"
             f"<div><span>SPY 연평균</span><b class=big>{sgn(cstats.get('SPY',{}).get('cagr'))}</b></div>"
             f"<div><span>QQQ 연평균</span><b class=big>{sgn(cstats.get('QQQ',{}).get('cagr'))}</b></div>"
             f"<div><span>최대 낙폭</span><b class=big>{sgn(st.get('mdd'))}</b></div>"
             f"<div><span>거래 수 · 승률</span><b class=big>{tt.get('n',0)} · {f_(tt.get('win'),0,True)}</b></div>"
             f"<div><span>평균 손익 (R)</span><b class=big>{f_(tt.get('avgR'),2)}R</b></div></div>")
    P.append(f"<div class='card warn' style='margin-top:10px'>{survivors_note}</div>")
    P.append("<h2>자산 곡선 (시작 100, 로그 축)</h2><div class=card>" + line_svg(curves) + "</div>")
    # 비교표
    P.append("<h2>전략 vs 지수</h2><div class=tw><table><tr><th class=l></th><th>총수익</th><th>연평균</th><th>최대 낙폭</th><th>변동성</th><th>연평균/최대낙폭</th></tr>")
    for k, v in cstats.items():
        P.append(f"<tr><td class=l><b>{e(k)}</b></td><td>{sgn(v.get('total'),0)}</td><td>{sgn(v.get('cagr'))}</td><td>{sgn(v.get('mdd'))}</td>"
                 f"<td>{f_(v.get('vol'),1,True)}</td><td>{f_(v.get('mar'),2)}</td></tr>")
    P.append("</table></div>")
    # 점수 기준별
    P.append("<h2>점수 기준을 바꾸면</h2><div class=mut style='margin-bottom:6px'>같은 규칙에서 매수 점수 기준만 바꿔 다시 돌린 결과</div>"
             "<div class=tw><table><tr><th class=l>매수 기준</th><th>연평균</th><th>최대 낙폭</th><th>거래 수</th><th>승률</th><th>평균 R</th><th>수익 팩터</th></tr>")
    for k, (c_, t_) in variants.items():
        P.append(f"<tr><td class=l>{e(k)}</td><td>{sgn(c_.get('cagr'))}</td><td>{sgn(c_.get('mdd'))}</td><td>{t_.get('n',0)}</td>"
                 f"<td>{f_(t_.get('win'),0,True)}</td><td>{f_(t_.get('avgR'),2)}</td><td>{f_(t_.get('pf'),2)}</td></tr>")
    P.append("</table></div>")
    # 점수 구간별 신호 성과 (자금 제약 없이 모든 신호)
    P.append("<h2>점수가 높을수록 결과가 좋았나</h2><div class=mut style='margin-bottom:6px'>자금·종목 수 제한 없이 모든 신호를 같은 손절 규칙으로 따라갔을 때. "
             "R = 초기 손절폭 대비 손익 (1R = 손절폭만큼 벌었음). 점수가 의미 있다면 아래로 갈수록 평균 R이 커져야 합니다.</div>")
    def ttab(rows, head):
        out = [f"<div class=tw><table><tr><th class=l>{head}</th><th>신호 수</th><th>승률</th><th>평균 R</th><th>중앙값 R</th><th>평균 수익 R</th><th>평균 손실 R</th><th>3R 이상</th><th>평균 보유(주)</th><th>평균 수익률</th></tr>"]
        for k, s in rows:
            out.append(f"<tr><td class=l>{e(k)}</td><td>{s.get('n',0)}</td><td>{f_(s.get('win'),0,True)}</td><td>{sgn(s.get('avgR'),2,False)}</td>"
                       f"<td>{f_(s.get('medR'),2)}</td><td>{f_(s.get('avgW'),2)}</td><td>{f_(s.get('avgL'),2)}</td><td>{f_(s.get('big'),0,True)}</td>"
                       f"<td>{f_(s.get('weeks'),0)}</td><td>{sgn(s.get('ret'),1)}</td></tr>")
        out.append("</table></div>")
        return "".join(out)
    P.append(ttab(buckets, "점수 구간"))
    P.append("<h2>신호 종류별</h2>" + ttab(sigtab, "신호"))
    # 연도별
    P.append("<h2>연도별 수익률</h2><div class=tw><table><tr><th class=l>연도</th>" +
             "".join(f"<th>{e(k)}</th>" for k in yearly.columns) + "</tr>")
    for yr, r in yearly.iterrows():
        P.append(f"<tr><td class=l>{yr}</td>" + "".join(f"<td>{sgn(v)}</td>" for v in r.values) + "</tr>")
    P.append("</table></div>")
    # 최근 거래
    P.append("<h2>최근 신호 30개</h2><div class=tw><table><tr><th class=l>티커</th><th class=l>신호일</th><th class=l>신호</th><th>점수</th><th>진입</th><th>청산</th><th>R</th><th>수익률</th><th class=l>청산 사유</th></tr>")
    for _, r in sig.sort_values("sig_date").tail(30).iloc[::-1].iterrows():
        P.append(f"<tr><td class=l><b>{e(r['ticker'])}</b></td><td class=l>{r['sig_date'].date()}</td><td class=l>{e(r['signal'])}</td><td>{r['score']:.0f}</td>"
                 f"<td>{f_(r['entry'],2)}</td><td>{f_(r['exit'],2)}</td><td>{sgn(r['R'],2,False)}</td><td>{sgn(r['ret'])}</td><td class=l>{e(r['why'])}</td></tr>")
    P.append("</table></div>")
    P.append("<h2>읽는 법과 한계</h2><div class=card><ul>"
             "<li>각 주의 판단은 그 주 금요일까지의 데이터만 쓰고, 매수는 다음 주 시가로 했습니다(미래 정보 미사용).</li>"
             "<li>손절은 주중 저가가 손절가에 닿으면 손절가(갭 하락이면 시가)에 판 것으로 계산했습니다.</li>"
             "<li>차트를 눈으로 보는 판단(패턴 모양, 매물대 두께)은 규칙으로 근사했을 뿐이라 실제 판단과 다를 수 있습니다.</li>"
             "<li>배당은 지수 비교(SPY·QQQ)에만 반영되고 전략 쪽 개별 종목은 수정주가 기준입니다.</li>"
             "<li>과거 결과는 미래 수익을 보장하지 않습니다.</li></ul></div></div>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(P))


# ---------------- 메인 ----------------
def main(argv=None, fetch=None, universe=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2006-01-01")
    ap.add_argument("--min-score", type=int, default=70)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)
    start = pd.Timestamp(args.start)
    uni = universe or S.load_universe(args.quick)
    uni = {k: v for k, v in uni.items() if v.get("bench") == "us"}
    years = (pd.Timestamp.today() - start).days / 365.25 + 3      # 지표 계산용 3년 더
    need = list(uni) + list(S.SECTOR_ETF.values()) + list(S.MARKET) + ["SPY", "QQQ"]
    print(f"{len(need)}개 종목의 약 {years:.0f}년치 주봉을 받습니다 (처음엔 5~15분 걸릴 수 있어요)")
    dfs = (fetch or (lambda n: S.fetch_weekly(n, years=years)))(need)
    if "^GSPC" not in dfs:
        sys.exit("S&P500 지수 데이터를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")
    frames, market = build_frames(dfs, uni)
    cal = dfs["^GSPC"].index[dfs["^GSPC"].index >= start]
    print("신호 모으고 거래 따라가는 중...")
    sig = collect_signals(frames, start)
    if sig.empty:
        sys.exit("기간 안에 신호가 하나도 없습니다. --start 날짜나 점수 기준을 확인하세요.")
    eq, trades = run_portfolio(sig, frames, cal, args.min_score)
    curves = {"전략": eq["equity"]}
    for b in ("SPY", "QQQ"):
        if b in dfs:
            c = dfs[b]["Close"].reindex(cal, method="ffill")
            curves[b] = c / c.iloc[0] * 100
    rets = pd.concat([f["close"].reindex(cal, method="ffill").pct_change() for f in frames.values()], axis=1)
    ew = (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod() * 100
    curves["동일가중(같은 종목들)"] = ew
    cstats = {k: stats_curve(v) for k, v in curves.items()}
    tstats = stats_trades(trades)
    variants = {}
    for ms in sorted({65, 70, 75, 80, args.min_score}):
        e2, t2 = run_portfolio(sig, frames, cal, ms)
        variants[f"{ms}점 이상"] = (stats_curve(e2["equity"]), stats_trades(t2))
    clean = sig[~sig["dup"]]
    bins = [(50, 65, "50–64 관찰"), (65, 75, "65–74 조건부"), (75, 80, "75–79"), (80, 90, "80–89 적극"), (90, 101, "90+")]
    buckets = [(lab, stats_trades(clean[(clean["score"] >= a) & (clean["score"] < b)])) for a, b, lab in bins]
    sigtab = [(k, stats_trades(clean[clean["signal"] == k])) for k in BT["signals"]]
    yearly = pd.DataFrame({k: v.resample("YE").last().pct_change() * 100 for k, v in curves.items()})
    first = {k: (v.resample("YE").last().iloc[0] / v.iloc[0] - 1) * 100 for k, v in curves.items()}
    yearly.iloc[0] = pd.Series(first)
    yearly.index = yearly.index.year
    note = ("<b>생존 편향 주의:</b> 종목 목록이 '지금' S&P500·나스닥100에 들어 있는 회사들이라, 그사이 망하거나 지수에서 빠진 회사는 빠져 있습니다. "
            "그래서 결과가 실제보다 좋게 나옵니다. '동일가중(같은 종목들)' 줄이 이 편향이 섞인 기준선이니, "
            "전략이 SPY보다 나은지뿐 아니라 이 줄보다 나은지도 함께 보세요.")
    os.makedirs(S.OUT, exist_ok=True)
    day = dt.date.today().isoformat()
    path = os.path.join(S.OUT, f"backtest_{day}.html")
    write_report(path, args, curves, cstats, tstats, variants, buckets, sigtab, yearly.round(1), sig, note)
    sig.to_csv(os.path.join(S.OUT, f"backtest_signals_{day}.csv"), index=False, encoding="utf-8-sig")
    if len(trades):
        trades.to_csv(os.path.join(S.OUT, f"backtest_trades_{day}.csv"), index=False, encoding="utf-8-sig")
    print(f"\n완료 · 전략 연평균 {f_(cstats['전략'].get('cagr'))}% · SPY {f_(cstats.get('SPY',{}).get('cagr'))}% · 거래 {tstats.get('n',0)}건")
    print("리포트:", path)
    if not args.no_open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(path))
    return path, cstats, tstats, sig, trades, eq


if __name__ == "__main__":
    main()

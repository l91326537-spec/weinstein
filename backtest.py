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
        scores, sigs, scores2, sigs2 = [], [], [], []
        for _, r in f.iterrows():
            if pd.isna(r["stage"]) or pd.isna(r["rs"]):
                scores.append(np.nan); sigs.append(""); scores2.append(np.nan); sigs2.append(""); continue
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
            g2 = S.signal_v2(d)
            sc2 = S.score_v2(d, ms, ss, None if pd.isna(r["sec_rs"]) else r["sec_rs"], g2)[0]
            if ss is None:
                sc2 = min(100, sc2 + round(BT["no_sector_pts"] * 0.8))
            scores2.append(sc2 if ok else np.nan)
            sigs2.append(g2 if ok else "")
        f["score1"], f["signal1"], f["score2"], f["signal2"] = scores, sigs, scores2, sigs2
        frames[t] = f
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(uni)}")
    return frames, market


# ---------------- 손절 규칙 후보 ----------------
EXITS = {
    "A 지금 규칙": dict(be_R=1, be_weeks=3, trail="low8ma", desc="1R 수익이나 3주 수익이면 본전 → 30주선·8주 저점 중 높은 쪽 아래로 추적"),
    "B 30주선 추적": dict(be_R=None, be_weeks=None, trail="ma", desc="본전 이동 없음, 손절을 30주선 2% 아래로만 따라 올림"),
    "C 30주선 종가 이탈": dict(be_R=None, be_weeks=None, trail="none", close_exit=True, desc="초기 손절 + 주봉 종가가 30주선 아래면 다음 주 매도"),
    "D 2R 후 30주선": dict(be_R=2, be_weeks=None, trail="ma", trail_after_be=True, desc="2R 수익이 나면 본전, 그 뒤 30주선 2% 아래로 추적"),
    "E 4단계까지 보유": dict(be_R=None, be_weeks=None, trail="none", desc="초기 손절만 두고, 종목이 4단계가 될 때까지 보유"),
}
EXIT = dict(EXITS["A 지금 규칙"])


# ---------------- 거래 한 건 시뮬레이션 ----------------
STOP = {"mode": "base"}     # base = 최근 10주 저점 아래 / entry = 매수 방식별(돌파 -N%, 풀백 = 3주 저점·30주선 아래)


def simulate_trade(f, i, signal=""):
    """i주 신호 → i+1주 시가 매수. (진입일, 청산일, 진입가, 청산가, R배수, 보유주, 청산사유, 초기손절) 또는 None"""
    if i + 1 >= len(f):
        return None
    o, hi, lo, c = f["open"].values, f["high"].values, f["low"].values, f["close"].values
    ma, low8, stg = f["ma30"].values, f["low8"].values, f["stage"].values
    e = o[i + 1]
    s = f["stop"].values[i]
    if STOP["mode"] == "entry" and signal in ("돌파", "풀백"):
        if signal == "돌파":
            s = e * (1 - STOP["pct"] / 100)
        else:
            s = min(f["low3"].values[i] * 0.99, f["ma30"].values[i] * 0.98)
    if e > 0 and s > e * (1 - BT["min_stop_pct"] / 100):
        s = e * (1 - BT["min_stop_pct"] / 100)
    if not (e > s > 0) or (e - s) / e * 100 > BT["max_stop_pct"]:
        return None
    risk = e - s
    s0 = s
    be = False
    X = EXIT
    j = i + 1
    while j < len(f):
        if lo[j] <= s:                                  # 손절 (갭 하락이면 시가)
            px = min(s, o[j])
            return (f.index[i + 1], f.index[j], e, px, (px - e) / risk, j - i - 1, "손절" if px < e else "추적 손절", s0)
        held = j - i
        if not be and X.get("be_R") is not None and (c[j] >= e + X["be_R"] * risk or (X.get("be_weeks") and held >= X["be_weeks"] and c[j] > e)):
            be = True
            s = max(s, e * (1 + 2 * BT["cost_pct"] / 100))
        m = ma[j] if not math.isnan(ma[j]) else 0
        if X["trail"] == "low8ma" and be:
            s = max(s, max(m, low8[j] if not math.isnan(low8[j]) else 0) * 0.99)
        elif X["trail"] == "ma" and (be or not X.get("trail_after_be")):
            s = max(s, m * 0.98)
        if X.get("close_exit") and m and c[j] < m and j + 1 < len(f):   # 주봉 종가 30주선 이탈
            px = o[j + 1]
            return (f.index[i + 1], f.index[j + 1], e, px, (px - e) / risk, j - i, "30주선 종가 이탈", s0)
        if stg[j] == 4 and j + 1 < len(f):           # 4단계 → 다음 주 시가 매도
            px = o[j + 1]
            return (f.index[i + 1], f.index[j + 1], e, px, (px - e) / risk, j - i, "4단계 진입", s0)
        j += 1
    return (f.index[i + 1], None, e, c[-1], (c[-1] - e) / risk, len(f) - i - 2, "보유 중", s0)


def collect_signals(frames, start, min_score_floor=50, ver=1):
    rows = []
    for t, f in frames.items():
        f["score"], f["signal"] = f[f"score{ver}"], f[f"signal{ver}"]
        idx = np.where((f["signal"].isin(BT["signals"])) & (f["score"] >= min_score_floor) & (f.index >= start))[0]
        last = -99
        for i in idx:
            dedupe = i - last <= 4                      # 4주 안에 반복된 신호는 같은 셋업으로 보고 통계에서 제외
            last = i
            tr = simulate_trade(f, i, f["signal"].iloc[i])
            if tr is None:
                continue
            ent, ex, e, px, R, wk, why, s0 = tr
            rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px,
                             stop0=s0,
                             R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why,
                             score=float(f["score"].iloc[i]), signal=f["signal"].iloc[i],
                             stage_label=f["stage_label"].iloc[i], dup=dedupe))
    return pd.DataFrame(rows)


# ---------------- 거래량·매수 시점 비교용 ----------------
def _hold_from(f, j0, e, s0, partial_first, vol_check):
    """j0주에 e로 매수(partial_first면 그 주 중간 매수). 초기 손절만 두고 4단계까지 보유. vol_check면 매수 주 거래량 확인"""
    o, lo, c, stg, vr = f["open"].values, f["low"].values, f["close"].values, f["stage"].values, f["vr"].values
    risk = e - s0
    j = j0
    if partial_first:
        if c[j] < s0:                                   # 매수한 주에 바로 손절 (장중 순서를 모르니 종가로 판단)
            return (f.index[j], f.index[j], e, s0, -1.0, 0, "손절", s0)
        if vol_check and (vr[j] if not math.isnan(vr[j]) else 0) < vol_check and j + 1 < len(f):
            px = o[j + 1]
            return (f.index[j], f.index[j + 1], e, px, (px - e) / risk, 1, "돌파 거래량 부족", s0)
        j += 1
    while j < len(f):
        if lo[j] <= s0:
            px = min(s0, o[j])
            return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "손절", s0)
        if stg[j] == 4 and j + 1 < len(f):
            px = o[j + 1]
            return (f.index[j0], f.index[j + 1], e, px, (px - e) / risk, j - j0 + 1, "4단계 진입", s0)
        j += 1
    return (f.index[j0], None, e, c[-1], (c[-1] - e) / risk, len(f) - 1 - j0, "보유 중", s0)


def _bounce(f, i, quiet):
    """30주선 부근 반등: 최근 3주 안에 30주선 +3% 이내까지 눌렸다가, 이번 주 거래량 늘며 양봉으로 반등 (지난주보다 높게 마감)"""
    o, l, c, ma, vr, st, sl = f["open"].values, f["low"].values, f["close"].values, f["ma30"].values, f["vr"].values, f["stage"].values, f["slope"].values
    if i < 4 or st[i] != 2 or not (sl[i] > 0) or math.isnan(ma[i]):
        return False
    touch = min(l[i - 2:i + 1]) <= ma[i] * 1.03
    held = min(c[i - 2:i + 1]) >= ma[i] * 0.97                     # 종가로 30주선을 크게 깨지 않음
    up = c[i] > o[i] and c[i] > c[i - 1] and ma[i] < c[i] <= ma[i] * 1.08
    vol = vr[i] >= 1.3 and vr[i] > vr[i - 1]
    if not (touch and held and up and vol):
        return False
    if quiet and not (np.nanmean(vr[i - 3:i]) < 1.0):              # 눌리는 동안은 거래량이 줄었어야
        return False
    return True


def collect_variant(frames, start, vol_rule, entry_rule, min_score_floor=50, pb_rule="near"):
    """pb_rule: 'near'(30주선 근처면 매수) | 'bounce'(근처에서 거래량 늘며 반등한 주 다음 주 시가) |
       'quiet'(bounce + 눌림 동안 거래량 감소) | 'bstop'(bounce 주 고가 +0.5% 위로 올라갈 때 매수) | 'none'(풀백 안 함)"""
    """vol_rule: 'base'(돌파 1.5배) | 'strict'(돌파 2배·풀백은 거래량 감소)
       entry_rule: 'monday'(신호 다음 주 시가) | 'stop'(저항선 +0.5% 매수 스톱) | 'stopvol'(매수 스톱 + 그 주 거래량 2배 미만이면 다음 주 매도)"""
    pct = S.CFG["breakout_stop_pct"]
    rows = []
    for t, f in frames.items():
        sc, sg = f["score2"].values, f["signal2"].values
        o, h, c, res, vr, bvr = f["open"].values, f["high"].values, f["close"].values, f["res"].values, f["vr"].values, f["brk_vr"].values
        st, slope, ma, low3 = f["stage"].values, f["slope"].values, f["ma30"].values, f["low3"].values
        idx_ok = f.index >= start
        last_entry = -99
        for i in range(30, len(f) - 1):
            if not idx_ok[i] or not (sc[i] >= min_score_floor):
                continue
            g = sg[i]
            if pb_rule != "near":
                if g == "풀백":
                    g = ""
                if pb_rule != "none" and _bounce(f, i, pb_rule == "quiet"):
                    if i - last_entry > 4:
                        e = o[i + 1]
                        s0 = min(min(low3[i] * 0.99, ma[i] * 0.98), e * (1 - BT["min_stop_pct"] / 100))
                        tr = None
                        if pb_rule == "bstop":
                            trig = h[i] * 1.005
                            if h[i + 1] >= trig and o[i + 1] <= trig * 1.03:
                                e = max(o[i + 1], trig)
                                s0 = min(s0, e * (1 - BT["min_stop_pct"] / 100))
                                if (e - s0) / e * 100 <= BT["max_stop_pct"]:
                                    tr = _hold_from(f, i + 1, e, s0, True, None)
                        elif e > s0 > 0 and (e - s0) / e * 100 <= BT["max_stop_pct"]:
                            tr = _hold_from(f, i + 1, e, s0, False, None)
                        if tr:
                            last_entry = i
                            ent, ex, e, px, R, wk, why, s0 = tr
                            rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                             R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why, score=float(sc[i]),
                                             signal="풀백", stage_label=f["stage_label"].iloc[i], dup=False))
            # 풀백: 신호 다음 주 시가 매수
            if g == "풀백":
                if vol_rule == "strict" and not (vr[i] < 1.0):
                    continue
                if i - last_entry <= 4:
                    continue
                e = o[i + 1]
                s0 = min(min(low3[i] * 0.99, ma[i] * 0.98), e * (1 - BT["min_stop_pct"] / 100))
                if not (e > s0 > 0) or (e - s0) / e * 100 > BT["max_stop_pct"]:
                    continue
                tr = _hold_from(f, i + 1, e, s0, False, None)
            elif g == "돌파" and entry_rule == "monday":
                if vol_rule == "strict" and not (bvr[i] >= 2.0):
                    continue
                if i - last_entry <= 4:
                    continue
                e = o[i + 1]
                s0 = e * (1 - pct / 100)
                tr = _hold_from(f, i + 1, e, s0, False, None)
            else:
                tr = None
            if tr:
                last_entry = i
                ent, ex, e, px, R, wk, why, s0 = tr
                rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                 R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why, score=float(sc[i]),
                                 signal=g, stage_label=f["stage_label"].iloc[i], dup=False))
            # 매수 스톱: 지난주까지 저항선 아래 7% 이내 + 이번 주 고가가 저항선 +0.5%를 넘으면 그 가격에 매수
            if entry_rule in ("stop", "stopvol"):
                j = i + 1
                if j - last_entry <= 4 or not (st[i] in (1, 2)) or not (slope[i] >= -S.CFG["flat_pct"]):
                    continue
                r_ = res[j]
                if math.isnan(r_) or not (r_ * 0.93 <= c[i] < r_):
                    continue
                trig = r_ * 1.005
                if h[j] < trig or o[j] > r_ * (1 + S.CFG["breakout_chase_pct"] / 100):
                    continue
                e = max(o[j], trig)
                s0 = e * (1 - pct / 100)
                tr = _hold_from(f, j, e, s0, True, 2.0 if entry_rule == "stopvol" else None)
                last_entry = j
                ent, ex, e, px, R, wk, why, s0 = tr
                rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                 R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why, score=float(sc[i]),
                                 signal="돌파", stage_label=f["stage_label"].iloc[i], dup=False))
    return pd.DataFrame(rows)


# ---------------- 포트폴리오 ----------------
def _victim(pos, frames, d, mode, new_score):
    """갈아타기: 팔 보유 종목 고르기 (지난주 종가까지의 정보만 사용, 4주 이상 보유한 종목만)"""
    best, best_sc = None, None
    for t, p in pos.items():
        if (d - p["r"]["entry_date"]).days < 28:
            continue
        f = frames[t]
        i = f.index.searchsorted(d) - 1
        if i < 0:
            continue
        sc = f["score2"].iloc[i]; st = f["stage"].iloc[i]; rs = f["rs"].iloc[i]; rs4 = f["rs4"].iloc[i]; ss = f["sec_stage"].iloc[i]
        sc = -1 if pd.isna(sc) else sc
        if mode == "weak":
            ok = sc < 50 or st == 3 or (pd.notna(rs) and pd.notna(rs4) and rs < 0 and rs < rs4) or ss in (3, 4)
        else:   # "gap": 새 종목보다 25점 이상 낮은 종목
            ok = new_score - sc >= 25
        if ok and (best is None or sc < best_sc):
            best, best_sc = t, sc
    return best


def run_portfolio(sig, frames, cal, min_score, max_pos=None, rotate=None, rot_min=80, fill=None):
    """fill: 남는 돈을 ETF로 채우기. dict(o=시가, c=종가, mkt=시장 단계, frac=채울 비율, half3=3단계면 절반, filt=4단계면 매도, band=리밸런싱 폭%)"""
    mp = max_pos or BT["max_pos"]
    etf_sh, inv_sum = 0.0, 0.0
    cands = sig[(sig["score"] >= min_score)].sort_values(["entry_date", "score"], ascending=[True, False])
    by_week = {d: g for d, g in cands.groupby("entry_date")}
    cash, equity = 100.0, 100.0
    pos = {}       # ticker -> dict(shares, exit_date, exit, frame)
    curve, trades = [], []
    cost = BT["cost_pct"] / 100
    wcap = min(BT["max_weight"], 100.0 / mp)        # 한 종목 비중 상한: 25%와 1/보유종목수 중 작은 값
    for d in cal:
        eo = None
        if fill is not None:
            eo = fill["o"].asof(d)
            if pd.isna(eo):
                eo = None
        # 0) ETF 맞추기: 지난주 시장 단계를 보고 이번 주 시가에 (차이가 band% 넘을 때만)
        if eo is not None:
            mprev = fill["mkt"][fill["mkt"].index < d]
            ms = mprev.iloc[-1] if len(mprev) else np.nan
            frac = fill["frac"]
            if fill.get("filt", True) and ms == 4:
                frac = 0.0
            elif fill.get("half3") and ms == 3:
                frac = frac / 2
            etf_v = etf_sh * eo
            target = frac * (cash + etf_v)
            if abs(target - etf_v) > equity * fill.get("band", 10) / 100 or (target == 0 and etf_v > 0):
                dv = target - etf_v
                cash -= dv + abs(dv) * cost
                etf_sh = target / eo
        # 1) 이번 주 시가 매수 (지난주까지의 자금으로), 자리가 꽉 찼으면 갈아타기 규칙 확인
        if d in by_week:
            for _, r in by_week[d].iterrows():
                if r["ticker"] in pos:
                    continue
                if len(pos) >= mp:
                    if not rotate or r["score"] < rot_min:
                        continue
                    v = _victim(pos, frames, d, rotate, r["score"])
                    if v is None:
                        continue
                    px = frames[v]["open"].reindex([d]).iloc[0]
                    if pd.isna(px):
                        continue
                    p = pos.pop(v)
                    cash += p["shares"] * px * (1 - cost)
                    rr = p["r"].copy()
                    risk0 = rr["entry"] - rr["stop0"]
                    rr["exit"], rr["exit_date"], rr["why"] = px, d, "갈아타기"
                    rr["R"] = (px - rr["entry"]) / risk0 if risk0 > 0 else 0
                    rr["ret"] = (px / rr["entry"] - 1) * 100 - 2 * BT["cost_pct"]
                    trades.append(rr)
                risk_ps = r["entry"] - r["stop0"]
                if risk_ps <= 0:
                    continue
                shares = equity * BT["risk_pct"] / 100 / risk_ps
                liquid = cash + (etf_sh * eo if eo is not None else 0)
                val = min(shares * r["entry"], equity * wcap / 100, liquid / (1 + 2 * cost))
                if val < equity * 0.01:
                    continue
                shares = val / r["entry"]
                cash -= val * (1 + cost)
                if cash < 0 and eo is not None:      # 모자라는 돈은 ETF를 팔아서
                    sell = min(-cash / (1 - cost), etf_sh * eo)
                    etf_sh -= sell / eo
                    cash += sell * (1 - cost)
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
        ev = 0.0
        if fill is not None and etf_sh:
            ec = fill["c"].asof(d)
            ev = etf_sh * (ec if pd.notna(ec) else (eo or 0))
        equity = cash + mv + ev
        curve.append((d, equity, len(pos), mv / equity * 100 if equity else 0, ev / equity * 100 if equity else 0))
    eq = pd.DataFrame(curve, columns=["date", "equity", "n", "stk", "etf"]).set_index("date")
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
    cols = ["#1d7a4b", "#4f6d84", "#b0741a", "#8e5cc8", "#c0433f", "#2a9db0", "#8a8f8c"]
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


def write_report(path, args, curves, cstats, tstats, variants, buckets, sigtab, yearly, sig, survivors_note, comp=None, best=None, exit_curves=None, seg=None):
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
         ("<a href='../' style='display:inline-block;margin-bottom:10px;font-weight:700;color:var(--g)'>← 스테이지 앱</a>" if getattr(args, "web", False) else "") +
         f"<h1>와인스테인 백테스트</h1><div class=mut>{e(args.start)} ~ {dt.date.today()} · 점수 {args.min_score}점 이상 · "
         f"신호 {', '.join(BT['signals'])} · 1회 손절 시 계좌 {BT['risk_pct']}% · 최대 {BT['max_pos']}종목 · 비용 편도 {BT['cost_pct']}%</div>"]
    if comp:
        P.append("<h2>풀백 매수 기준 비교</h2><div class=mut style='margin-bottom:6px'>모두 6종목 · 남는 돈은 QQQ(시장 4단계면 매도) · 돌파는 저항선 +0.5% 매수 스톱·손절 -5% · 4단계까지 보유. 풀백을 언제 사는지만 바꿨어요. 풀백 손절은 모두 최근 3주 저점 -1%와 30주선 -2% 중 낮은 쪽. "
                 f"아래 나머지 표는 연평균÷최대낙폭이 가장 좋았던 <b>{e(best)}</b> 기준입니다. '75점+ R'이 '65점 미만 R'보다 커야 점수표가 의미 있어요.</div>"
                 "<div class=tw><table><tr><th class=l>조합</th><th>연평균</th><th>최대 낙폭</th><th>거래</th><th>승률</th><th>평균 R</th><th>3R 이상</th><th>평균 보유(주)</th><th>75점+ R</th><th>65점 미만 R</th></tr>")
        for r in comp:
            c_, t_ = r["c"], r["t"]
            P.append(f"<tr><td class=l><b>{e(r['name'])}</b>{' ★' if r['name'] == best else ''}<div class=mut style='font-size:12px;white-space:normal;min-width:220px'>{e(r['desc'])}</div></td>"
                     f"<td>{sgn(c_.get('cagr'))}</td><td>{sgn(c_.get('mdd'))}</td><td>{t_.get('n', 0)}</td><td>{f_(t_.get('win'), 0, True)}</td>"
                     f"<td>{f_(t_.get('avgR'), 2)}</td><td>{f_(t_.get('big'), 0, True)}</td><td>{f_(t_.get('weeks'), 0)}</td>"
                     f"<td>{f_(r.get('hiR'), 2)}</td><td>{f_(r.get('loR'), 2)}</td></tr>")
        P.append("</table></div>")
        if exit_curves:
            P.append("<div class=card style='margin-top:8px'>" + line_svg(exit_curves) + "</div>")
    if seg:
        P.append(f"<h2>대형 · 중형 · 소형 비교</h2><div class=mut style='margin-bottom:6px'>{e(best or '')} 규칙으로 크기별 종목만 따로 돌린 결과. "
                 "대형=S&P500·나스닥100, 중형=S&P400, 소형=러셀2000. 소형주일수록 생존 편향이 커서 실제보다 좋게 나오는 점을 감안하세요.</div>"
                 "<div class=tw><table><tr><th class=l>구분</th><th>종목 수</th><th>연평균</th><th>최대 낙폭</th><th>거래</th><th>승률</th><th>평균 R</th><th>신호당 평균 R</th><th>신호 3R 이상</th></tr>")
        for r in seg:
            P.append(f"<tr><td class=l><b>{e(r['name'])}</b></td><td>{r['n_t']}</td><td>{sgn(r['c'].get('cagr'))}</td><td>{sgn(r['c'].get('mdd'))}</td>"
                     f"<td>{r['t'].get('n', 0)}</td><td>{f_(r['t'].get('win'), 0, True)}</td><td>{f_(r['t'].get('avgR'), 2)}</td>"
                     f"<td>{f_(r['s'].get('avgR'), 2)}</td><td>{f_(r['s'].get('big'), 0, True)}</td></tr>")
        P.append("</table></div>")
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
    ap.add_argument("--web", action="store_true", help="앱 안에 넣을 리포트 (앱으로 돌아가는 링크 포함)")
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
    print("풀백 매수 기준별로 돌리는 중... (매수: 돌파 매수 스톱·-5% / 풀백, 4단계까지 보유)")
    EXIT.clear(); EXIT.update(EXITS["E 4단계까지 보유"])
    STOP.clear(); STOP.update(mode="entry", pct=S.CFG["breakout_stop_pct"])
    base_sig = collect_variant(frames, start, "base", "stop")
    if base_sig.empty:
        sys.exit("기간 안에 신호가 하나도 없습니다. --start 날짜나 점수 기준을 확인하세요.")
    def F(etf, frac=1.0, half3=False, filt=True):
        if etf not in dfs:
            return "skip"
        x = dfs[etf]
        return dict(o=x["Open"], c=x["Close"], mkt=market, frac=frac, half3=half3, filt=filt, band=10)
    q = F("QQQ")
    sigs = {k: collect_variant(frames, start, "base", "stop", pb_rule=k) for k in ("near", "bounce", "quiet", "bstop", "none")}
    GRID = [  # (이름, 최대 보유 종목 수, 남는 돈 채우기, 설명, 신호)
        ("풀백: 30주선 근처면 매수 (지금)", 6, q, "주가가 30주선 -3%~+5% 안에 있으면 다음 주 시가 매수", "near"),
        ("풀백: 거래량 늘며 반등 확인", 6, q, "최근 3주 안에 30주선 +3% 이내까지 눌린 뒤, 거래량이 평균 1.3배 이상·전주보다 늘며 양봉으로 반등한 주 → 다음 주 시가 매수", "bounce"),
        ("풀백: 조용히 눌리고 거래량 반등", 6, q, "위 조건 + 눌리는 3주 동안 거래량이 평균보다 적었을 때만", "quiet"),
        ("풀백: 반등 주 고가 돌파 시 매수", 6, q, "반등 확인 주의 고가 +0.5% 매수 스톱 (다음 주에 못 넘으면 안 삼)", "bstop"),
        ("풀백 안 함 (돌파만)", 6, q, "돌파 매수만", "none"),
    ]
    comp, runs, combos = [], {}, {}
    for name, mp_, fl, why, sk in GRID:
        if fl == "skip":
            fl = None
        base_sig = sigs[sk]
        pbs = stats_trades(base_sig[(base_sig["signal"] == "풀백") & (base_sig["score"] >= args.min_score)])
        e_, t_ = run_portfolio(base_sig, frames, cal, args.min_score, max_pos=mp_, fill=fl)
        cs, ts = stats_curve(e_["equity"]), stats_trades(t_)
        desc = (f"{why} · 한 종목 최대 {min(BT['max_weight'], 100 / mp_):.0f}% · 평균 보유 {e_['n'].mean():.1f}종목 · "
                f"평균 투자 비중: 종목 {e_['stk'].mean():.0f}% + QQQ {e_['etf'].mean():.0f}%"
                + (f" · 풀백 신호만 따로({args.min_score}점+, 전부 따라갔을 때): {pbs.get('n', 0)}개 · 승률 {f_(pbs.get('win'), 0)}% · 평균 {f_(pbs.get('avgR'))}R" if pbs.get("n") else ""))
        comp.append(dict(name=name, desc=desc, c=cs, t=ts, hiR=None, loR=None))
        runs[name] = (base_sig, e_, t_)
        combos[name] = (2, "entry", S.CFG["breakout_stop_pct"], "E 4단계까지 보유", mp_, fl)
        print(f"  {name}: 연평균 {f_(cs.get('cagr'))}% · 최대낙폭 {f_(cs.get('mdd'))}% · 풀백 {pbs.get('n', 0)}개 {f_(pbs.get('avgR'))}R 승률 {f_(pbs.get('win'), 0)}%")
    if not runs:
        sys.exit("기간 안에 신호가 하나도 없습니다. --start 날짜나 점수 기준을 확인하세요.")
    best = max(comp, key=lambda r: (r["c"].get("mar") if pd.notna(r["c"].get("mar")) else -99))["name"]   # 연평균/최대낙폭이 가장 좋은 조합
    ver_b, smode_b, pct_b, ex_b, mp_b, fill_b = combos[best]
    EXIT.clear(); EXIT.update(EXITS[ex_b])
    STOP.clear(); STOP.update(mode=smode_b, pct=pct_b)
    for f in frames.values():
        f["score"], f["signal"] = f[f"score{ver_b}"], f[f"signal{ver_b}"]
    sig, eq, trades = runs[best]
    capmap = {t: v.get("cap", "기타") for t, v in uni.items()}
    sig["cap"] = sig["ticker"].map(capmap)
    seg = []
    for cp in ("대형", "중형", "소형"):
        ss = sig[sig["cap"] == cp]
        if ss.empty:
            continue
        e3, t3 = run_portfolio(ss, frames, cal, args.min_score, max_pos=mp_b, fill=fill_b)
        cl3 = ss[~ss["dup"]]
        seg.append(dict(name=cp, n_t=int(ss["ticker"].nunique()), c=stats_curve(e3["equity"]), t=stats_trades(t3), s=stats_trades(cl3)))
        print(f"  {cp}: 연평균 {f_(seg[-1]['c'].get('cagr'))}% · 최대낙폭 {f_(seg[-1]['c'].get('mdd'))}%")
    curves = {"전략": eq["equity"]}
    for b in ("SPY", "QQQ"):
        if b in dfs:
            c = dfs[b]["Close"].reindex(cal, method="ffill")
            curves[b] = c / c.iloc[0] * 100
    rets = pd.concat([f["close"].reindex(cal, method="ffill").pct_change() for f in frames.values()], axis=1)
    rets = rets.replace([np.inf, -np.inf], np.nan).where(rets.abs() < 1.0)   # 상장 전후·데이터 오류로 생기는 극단값 제외
    ew = (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod() * 100
    curves["동일가중(같은 종목들)"] = ew
    cstats = {k: stats_curve(v) for k, v in curves.items()}
    tstats = stats_trades(trades)
    variants = {}
    for ms in sorted({65, 70, 75, 80, args.min_score}):
        e2, t2 = run_portfolio(sig, frames, cal, ms, max_pos=mp_b, fill=fill_b)
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
    exit_curves = {r["name"]: runs[r["name"]][1]["equity"] for r in comp}
    if "SPY" in curves:
        exit_curves["SPY"] = curves["SPY"]
    write_report(path, args, curves, cstats, tstats, variants, buckets, sigtab, yearly.round(1), sig, note,
                 comp=comp, best=best, exit_curves=exit_curves, seg=seg)
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

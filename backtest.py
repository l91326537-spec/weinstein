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
실행: python backtest.py                (기본: 2006년부터, 점수 80 이상)
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
        qL, qB, qP, qT, qPt = [], [], [], [], []
        qC, qD, qS, qSu = [], [], [], []
        ddv = f["dd52"].values
        isb = []
        for _, r in f.iterrows():
            if pd.isna(r["stage"]) or pd.isna(r["rs"]):
                scores.append(np.nan); sigs.append(""); scores2.append(np.nan); sigs2.append(""); qL.append(0); qB.append(0); qP.append(False); qT.append(0); qPt.append(0); isb.append(False); qC.append(0); qD.append(0); qS.append(0); qSu.append(0); continue
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
            q = S.quality_adj(d, g2)
            qL.append(1 if q["late"] else 0); qB.append(0 if not q["base"] else (1 if q["base"] > 0 else -1)); qP.append(q["pinned"])
            qT.append(q.get("trend", 0)); qPt.append(q.get("pat", 0)); isb.append(g2 == "돌파")
            qC.append(q.get("chop", 0)); qD.append(q.get("dump", 0)); qS.append(1 if q.get("sqz_on") else 0); qSu.append(q.get("sup", 0))
        f["score1"], f["signal1"], f["score2"], f["signal2"] = scores, sigs, scores2, sigs2
        L, B, P = np.array(qL, float), np.array(qB, float), np.array(qP, bool)
        s2 = f["score2"].values
        Bp = np.where(B > 0, 4, np.where(B < 0, -8, 0))
        T, Pt = np.array(qT, float), np.array(qPt, float)
        base = -8 * L + Bp
        dd = np.nan_to_num(ddv, nan=0.0)
        ov = np.where(dd <= -25, -10, np.where(dd <= -15, -5, 0))
        ovp = np.where(np.array(isb), 0, ov)
        f["sqz_on"] = np.array(qS, float)              # 볼린저 수축 뒤 돌파·풀백 (가점 크기와 무관)
        cur = base + T + Pt + f["sqz_on"].values * S.CFG["squeeze_pts"]      # 볼린저 수축 가점 (앱 점수와 같게)
        C, D, Su = np.array(qC, float), np.array(qD, float), np.array(qSu, float)
        if S.CFG["use_supply"]:
            cur = cur + Su                      # 앱과 같게
        for name, a in (("TPt", cur), ("C", cur + C), ("D", cur + D), ("CD", cur + C + D),
                        ("TPtS", cur if S.CFG["use_supply"] else cur + Su), ("TPtS0", cur - Su if S.CFG["use_supply"] else cur)):
            v = np.clip(s2 + a, 0, 100)
            f["score2" + name] = np.where(P, np.minimum(v, 20), v)
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
def _hold_from(f, j0, e, s0, partial_first, vol_check, be=None, mode="intra", dyn_ma=False, emerg=None, ma_exit=None, chand=None):
    """mode: 'intra'=장중 손절가 닿으면 매도(자동 매도) | 'close'=금요일 종가가 손절가 아래면 그 종가에 매도
       dyn_ma: 손절가 대신 '금요일 종가가 30주선 아래면 매도' (s0는 수량 계산용 명목 손절)"""
    ma_v = f["ma30"].values
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
    s_orig, moved = s0, False
    atr_v = f["atr10"].values if chand else None
    hh = e
    while j < len(f):
        if be and not moved and j - j0 >= be:
            moved = True
            if c[j - 1] > e:                 # 매수 후 be주째 종가가 수익이면 손절선을 매수가로
                s0 = max(s0, e)
        if dyn_ma:
            if c[j] < ma_v[j]:
                px = c[j]
                return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "30주선 아래 마감", s_orig)
        elif mode == "close":
            if emerg and lo[j] <= s0 * (1 - emerg / 100):        # 비상 손절: 손절가보다 emerg% 아래는 장중 자동 매도
                px = min(o[j], s0 * (1 - emerg / 100))
                return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "비상 손절", s_orig)
            if c[j] < s0:
                px = c[j]
                return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "손절(종가)", s_orig)
        elif lo[j] <= s0:
            px = min(s0, o[j])
            return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "손절" if s0 == s_orig else "본전 손절", s_orig)
        if chand:                               # 샹들리에: 매수 후 최고 종가 - ATR×chand 아래로 금요일 마감하면 매도
            hh = max(hh, c[j])
            if not math.isnan(atr_v[j]) and c[j] < hh - chand * atr_v[j]:
                return (f.index[j0], f.index[j], e, c[j], (c[j] - e) / risk, j - j0, "샹들리에 청산", s_orig)
        if ma_exit is not None and not math.isnan(ma_v[j]) and c[j] < ma_v[j] * (1 - ma_exit / 100):   # 금요일 종가가 30주선 -ma_exit% 아래
            px = c[j]
            return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "30주선 종가 이탈", s_orig)
        if stg[j] == 4 and j + 1 < len(f):
            px = o[j + 1]
            return (f.index[j0], f.index[j + 1], e, px, (px - e) / risk, j - j0 + 1, "4단계 진입", s0)
        j += 1
    return (f.index[j0], None, e, c[-1], (c[-1] - e) / risk, len(f) - 1 - j0, "보유 중", s0)


def ma_touch_study(frames, start, capmap, n=60, stop_pct=5.0, above_weeks=8):
    """n주선 터치 매수: n주선이 13주 전보다 높고(상승 중) 직전 above_weeks주 연속 종가가 n주선 위였는데, 이번 주 저가가 n주선 +1% 안까지 내려옴
       → n주선 가격(시가가 더 낮으면 시가)에 장중 매수. 손절 = 매수가 -stop_pct%, 금요일 종가 손절 + 비상 -3%, 4단계까지 보유"""
    rows = []
    for t, f in frames.items():
        o, lo, c = f["open"].values, f["low"].values, f["close"].values
        ma = f["close"].rolling(n).mean().values
        ma30, stg, rs, dv = f["ma30"].values, f["stage"].values, f["rs"].values, f["dollar_vol"].values
        N = len(f)
        busy = -1
        for i in range(n + 13, N):
            if i <= busy or f.index[i] < start or math.isnan(ma[i]) or not (ma[i] > ma[i - 13]):
                continue
            if not all(c[i - 1 - k] > ma[i - 1 - k] for k in range(above_weeks)) or not (lo[i] <= ma[i] * 1.01):
                continue
            if c[i] < S.CFG["min_price"] or (dv[i] if not math.isnan(dv[i]) else 0) < S.CFG["min_dollar_vol"]:
                continue
            e = min(o[i], ma[i] * 1.01)
            s0 = e * (1 - stop_pct / 100)
            tr = _hold_from(f, i, e, s0, True, None, mode="close", emerg=3)
            ent, ex, e_, px, R, wk, why, _ = tr
            busy = f.index.get_loc(ex) if ex is not None else N
            rows.append(dict(ticker=t, sig_date=f.index[i], R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why,
                             sm=capmap.get(t) == "소형", st=stg[i], rs=rs[i], u30=c[i] < ma30[i] if not math.isnan(ma30[i]) else False,
                             entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0, mkt=f["mkt"].iloc[i] if "mkt" in f else np.nan))
    return pd.DataFrame(rows)


def reclaim_study(frames, start, capmap, maxk=8):
    """30주선 아래로 1~maxk주 마감했다가 다시 위로 올라선 주에 매수. 손절 = 이탈 기간 최저가 -1%, 금요일 종가 손절 + 비상 -3%, 4단계까지 보유.
       close = 회복한 주 금요일 종가 매수 / intra = 그 주 장중 30주선 돌파 때 매수(그 주 종가가 다시 30주선 아래여도 산 것으로 침)"""
    rows = []
    for t, f in frames.items():
        o, h, lo, c, ma = (f[k].values for k in ("open", "high", "low", "close", "ma30"))
        sl, rs, dv = f["slope"].values, f["rs"].values, f["dollar_vol"].values
        n = len(f)
        busy = {"close": -1, "intra": -1}
        for i in range(maxk + 2, n - 1):
            if f.index[i] < start or math.isnan(ma[i]) or not (c[i - 1] < ma[i - 1]):
                continue
            k = 0
            while k < maxk + 1 and c[i - 1 - k] < ma[i - 1 - k]:
                k += 1
            if k == 0 or k > maxk or math.isnan(ma[i - 1 - k]) or not (sl[i - 1 - k] > 0):   # 이탈 전에는 30주선 위 · 30주선 상승 중이던 종목만
                continue
            if c[i] < S.CFG["min_price"] or (dv[i] if not math.isnan(dv[i]) else 0) < S.CFG["min_dollar_vol"]:
                continue
            low_k = np.nanmin(lo[i - k:i + 1])
            for mode in ("close", "intra"):
                if i <= busy[mode]:
                    continue
                if mode == "close":
                    if not (c[i] > ma[i]):
                        continue
                    e, j0, part = c[i], i + 1, False
                else:
                    if not (h[i] > ma[i]):
                        continue
                    e, j0, part = max(o[i], ma[i]), i, True
                s0 = low_k * 0.99
                if e > 0 and s0 > e * (1 - BT["min_stop_pct"] / 100):
                    s0 = e * (1 - BT["min_stop_pct"] / 100)
                if not (e > s0 > 0) or (e - s0) / e * 100 > BT["max_stop_pct"] or j0 >= n:
                    continue
                tr = _hold_from(f, j0, e, s0, part, None, mode="close", emerg=3)
                ent, ex, e_, px, R, wk, why, _ = tr
                busy[mode] = f.index.get_loc(ex) if ex is not None else n
                rows.append(dict(ticker=t, mode=mode, sig_date=f.index[i], k=k, up=sl[i] > 0, rs=rs[i], sm=capmap.get(t) == "소형",
                                 R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why, stop_pct=(e - s0) / e * 100))
    return pd.DataFrame(rows)


_FR, _SECMAP, _GOLD = {}, {}, {}


def big_winner_study(allsig, at, capmap, buckets):
    """큰 수익(3R+, 5R+) 거래와 나머지 거래의 특징 비교 (신호 시점 값)"""
    d = allsig.copy()
    d["rs_"], d["ext_"], d["mkt_"], d["sec_"], d["vr_"] = at("rs"), at("ext"), at("mkt"), at("sec_stage"), at("vr")
    d["stop_pct"] = (d["entry"] - d["stop0"]) / d["entry"] * 100
    d["cap_"] = d["ticker"].map(capmap).fillna("기타")
    big, huge = d["R"] >= 3, d["R"] >= 5
    print(f"  [큰 수익] 전체 {len(d)}건 · 3R+ {big.sum()}건 ({big.mean() * 100:.1f}%) · 5R+ {huge.sum()}건 ({huge.mean() * 100:.1f}%) · "
          f"3R+ 평균 보유 {d.loc[big, 'weeks'].mean():.0f}주 · 평균 수익 {d.loc[big, 'ret'].mean():.0f}%")
    for nm, col in (("RS", "rs_"), ("30주선 이격 %", "ext_"), ("신호 주 거래량 배수", "vr_"), ("돌파 매수 주 거래량 배수", "evr"),
                    ("기저 길이(주)", "bl"), ("30주선 상승 주수", "upw"), ("30주선 52주 상승률 %", "rise"), ("52주 고점 대비 %", "dd"),
                    ("점수", "score"), ("52주 30주선 이탈 횟수", "x52"), ("손절폭 %", "stop_pct")):
        a_, b_ = d.loc[big, col].dropna(), d.loc[~big, col].dropna()
        if len(a_) and len(b_):
            print(f"  [큰 수익] {nm}: 3R+ 중앙값 {a_.median():.1f} · 나머지 {b_.median():.1f} · 5R+ {d.loc[huge, col].median():.1f}")
    def share(lab, m):
        m = m.fillna(False).astype(bool)
        st = stats_trades(d[m])
        buckets.append(("[큰 수익] " + lab, st))
        print(f"  [큰 수익] {lab}: {m.sum()}건 · 3R+ 비율 {big[m].mean() * 100:.1f}% · 5R+ {huge[m].mean() * 100:.1f}% · 평균 {f_(st.get('avgR'))}R")
    for cp in ("대형", "중형", "소형"):
        share(f"{cp}주", d["cap_"] == cp)
    share("시장 2단계일 때", d["mkt_"] == 2); share("시장 2단계 아닐 때", d["mkt_"] != 2)
    share("업종 2단계일 때", d["sec_"] == 2); share("업종 2단계 아닐 때", d["sec_"] != 2)
    share("돌파 신호", d["signal"] == "돌파"); share("풀백 신호", d["signal"] == "풀백")
    share("1→2단계 돌파", d["kind"] == "B12"); share("2단계 중간 돌파", d["kind"] == "B2")
    share("RS 10 이상", d["rs_"] >= 10); share("RS 0~10", (d["rs_"] >= 0) & (d["rs_"] < 10)); share("RS 0 미만", d["rs_"] < 0)
    share("손절폭 5% 이하", d["stop_pct"] <= 5); share("손절폭 5~10%", (d["stop_pct"] > 5) & (d["stop_pct"] <= 10)); share("손절폭 10% 초과", d["stop_pct"] > 10)
    share("대·중형 · 기저 26주+ · 돌파 거래량 2배+ · RS 0+",
          (d["cap_"] != "소형") & (d["bl"] >= 26) & (d["evr"] >= 2) & (d["rs_"] >= 0) & d["kind"].isin(["B12", "B2"]))
    share("대·중형 · 시장·업종 모두 2단계 · RS 0+", (d["cap_"] != "소형") & (d["mkt_"] == 2) & (d["sec_"] == 2) & (d["rs_"] >= 0))
    # 차트 패턴 (신호 시점)
    if "hhhl" in next(iter(_FR.values())):
        d["hhhl_"], d["lh_"], d["dbl_"], d["tpl_"], d["tl_"] = at("hhhl"), at("lh"), at("dbl13"), at("tpl13"), at("tl13")
        share("고점·저점 모두 높아지는 중 (HH·HL)", d["hhhl_"] == 1); share("고점이 낮아지는 중 (LH)", d["lh_"] == 1)
        share("최근 13주 안 이중 바닥 돌파", d["dbl_"] == 1); share("최근 13주 안 삼중 바닥 돌파", d["tpl_"] == 1)
        share("최근 13주 안 하락 추세선 돌파", d["tl_"] == 1)
        share("패턴 없음 (위 넷 모두 아님)", (d["hhhl_"] != 1) & (d["dbl_"] != 1) & (d["tpl_"] != 1) & (d["tl_"] != 1))
    # 오를 때 거래량 많고 내릴 때 적은 종목 (최근 10주 상승주 거래량 합 ÷ 하락주 거래량 합)
    d["updn_"] = at("updn")
    a_, b_ = d.loc[big, "updn_"].dropna(), d.loc[~big, "updn_"].dropna()
    if len(a_):
        print(f"  [큰 수익] 상승/하락 거래량 비율(10주): 3R+ 중앙값 {a_.median():.2f} · 나머지 {b_.median():.2f}")
    for lab, lo, hi in (("0.9 미만 (내릴 때 거래량 더 많음)", 0, 0.9), ("0.9~1.1", 0.9, 1.1), ("1.1~1.5", 1.1, 1.5), ("1.5~2", 1.5, 2), ("2 이상 (오를 때 거래량 2배+)", 2, 99)):
        share(f"상승/하락 거래량 비율 {lab}", (d["updn_"] >= lo) & (d["updn_"] < hi))
    share("상승/하락 거래량 1.5+ · 대·중형 · RS 0+", (d["updn_"] >= 1.5) & (d["cap_"] != "소형") & (d["rs_"] >= 0))
    # 시장이 약할 때(2단계 아님) 나온 신호: 방어 업종·저베타 vs 그 외
    d["beta_"] = at("beta") if "beta" in next(iter(_FR.values())) else np.nan
    sec = d["ticker"].map(_SECMAP).fillna("")
    gold = d["ticker"].map(_GOLD).fillna(False).astype(bool)
    dfn = sec.isin(["Utilities", "Consumer Staples", "Health Care"]) | gold
    weak = d["mkt_"] != 2
    share("시장 약할 때 · 방어 업종(유틸·필수소비재·헬스케어·금광)", weak & dfn)
    share("시장 약할 때 · 그 외 업종", weak & ~dfn)
    share("시장 약할 때 · 베타 0.8 미만", weak & (d["beta_"] < 0.8))
    share("시장 약할 때 · 베타 0.8~1.3", weak & (d["beta_"] >= 0.8) & (d["beta_"] < 1.3))
    share("시장 약할 때 · 베타 1.3 이상", weak & (d["beta_"] >= 1.3))
    share("시장 약할 때 · 그 외 업종 · 대·중형 · RS 0+", weak & ~dfn & (d["cap_"] != "소형") & (d["rs_"] >= 0))
    share("시장 2단계 · 방어 업종", ~weak & dfn); share("시장 2단계 · 그 외 업종", ~weak & ~dfn)


def supply_study(frames, cal, base_run, sig_s, sig_s0, ms0, mp, fill, msmall, capmap, allsig, at, buckets):
    """위쪽 전고점 매물: 신호별 성과 + 감점 넣고/빼고 포트폴리오 비교. 반환: 곡선 표 행"""
    d = allsig
    d["room_"], d["supn_"], d["deep_"] = at("room"), at("sup_n"), at("deep")
    lg = d["ticker"].map(capmap) != "소형"
    near = (d["room_"] > 0) & (d["room_"] <= S.CFG["supply_room"])
    dense = d["supn_"] >= S.CFG["supply_weeks"]
    for nm, sel in (("돌파", d["kind"].isin(["B12", "B2"])), ("풀백", d["signal"] == "풀백"), ("전체", d["signal"] != "")):
        sel = sel & lg
        for lab, m in (("신고가권 (2년 고점 위)", d["room_"] <= 0), ("위 0~5%에 전고점", (d["room_"] > 0) & (d["room_"] <= 5)),
                       ("위 5~10%", (d["room_"] > 5) & (d["room_"] <= 10)), ("위 10~20%", (d["room_"] > 10) & (d["room_"] <= 20)),
                       ("위 20%+", d["room_"] > 20),
                       (f"위 10% 안 매물 {S.CFG['supply_weeks']}주+ (감점 대상)", near & dense & (d["deep_"] != 1)),
                       ("위 10% 안 매물 · 큰 하락 뒤 긴 기저 (감점 제외)", near & dense & (d["deep_"] == 1)),
                       ("위 10% 안 매물 1~2주", near & ~dense)):
            st = stats_trades(d[sel & m])
            buckets.append((f"[전고점 매물] {nm} · {lab} (대·중형)", st))
            print(f"  [전고점 매물] {nm} · {lab}: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R · 3R+ {f_(st.get('big'))}%")
    rows = []
    for nm, sg in (("감점 없음", sig_s0), (f"감점 -{S.CFG['supply_pts']}", sig_s)):
        e_, t_ = run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
        rows.append(curve_row(f"[전고점 매물] {nm}", f"위 {S.CFG['supply_room']:.0f}% 안 2년 내 전고점 {S.CFG['supply_weeks']}주+ · 큰 하락 뒤 긴 기저 제외 · 거래 {len(t_)}건", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}%")
    return rows


def event_study(allsig, capmap, buckets, days=90):
    """돌파 매수 주 거래량이 큰(2배+) 대·중형 돌파 중, 직전 90일 안에 인수합병·주식교환·공개매수 공시가 있던 것 vs 없던 것"""
    d = allsig[allsig["kind"].isin(["B12", "B2"]) & (allsig["ticker"].map(capmap) != "소형") & (allsig["evr"] >= 2)].copy()
    tks = sorted(set(d["ticker"]))
    print(f"  [이벤트 거래량] SEC 공시 확인 {len(tks)}종목...")
    evs = {}
    for i, t in enumerate(tks):
        e = S.sec_events(t, full=True)
        if e is None and not S._SEC["ok"]:
            print("  [이벤트 거래량] SEC 접속 실패 → 건너뜀")
            return
        evs[t] = [pd.Timestamp(x[0]) for x in (e or [])]
        if (i + 1) % 200 == 0:
            print(f"    {i + 1}/{len(tks)}")
    def hit(t, dd):
        return any(0 <= (dd - x).days <= days for x in evs.get(t, []))
    d["ev"] = [hit(t, dd) for t, dd in zip(d["ticker"], d["entry_date"])]
    for lab, m in (("공시 있음 (이벤트 거래량 의심)", d["ev"]), ("공시 없음", ~d["ev"])):
        st = stats_trades(d[m])
        buckets.append((f"[이벤트 거래량] 거래량 2배+ 돌파 · 직전 {days}일 {lab} (대·중형)", st))
        print(f"  [이벤트 거래량] {lab}: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R · 3R+ {f_(st.get('big'))}%")


def breadth_study(frames, cal, dfs, market, base_run, ms0, mp, fill, msmall, capmap):
    """와인스타인식 시장 폭: 30주선 위 종목 비율, 52주 신고가-신저가. 지수 4단계 전에 미리 팔기 / 4단계 중에 미리 다시 사기"""
    C = pd.concat({t: f["close"] for t, f in frames.items()}, axis=1).reindex(cal)
    M = pd.concat({t: f["ma30"] for t, f in frames.items()}, axis=1).reindex(cal)
    H = pd.concat({t: f["high"].rolling(52, min_periods=40).max() for t, f in frames.items()}, axis=1).reindex(cal)
    L = pd.concat({t: f["low"].rolling(52, min_periods=40).min() for t, f in frames.items()}, axis=1).reindex(cal)
    Hi = pd.concat({t: f["high"] for t, f in frames.items()}, axis=1).reindex(cal)
    Lo = pd.concat({t: f["low"] for t, f in frames.items()}, axis=1).reindex(cal)
    ok = C.notna() & M.notna()
    n = ok.sum(axis=1).replace(0, np.nan)
    pa = ((C > M) & ok).sum(axis=1) / n * 100                         # 30주선 위 종목 비율 %
    nhnl = (((Hi >= H) & ok).sum(axis=1) - ((Lo <= L) & ok).sum(axis=1)) / n * 100
    nh4 = nhnl.rolling(4, min_periods=1).mean()
    pa10 = pa.rolling(10, min_periods=4).mean()
    fall, rise = pa < pa10, pa > pa10
    mk = market.reindex(cal, method="ffill")
    m4 = mk == 4
    print(f"  [시장 폭] 30주선 위 종목 비율 평균 {pa.mean():.0f}% · 최저 {pa.min():.0f}% ({pa.idxmin().date()}) · 지수 4단계 기간 {m4.mean() * 100:.0f}%")
    rules = [("지수 4단계면 매도 (지금)", m4),
             ("+ 30주선 위 비율 40% 미만·하락 중이면 미리 매도", m4 | ((pa < 40) & fall)),
             ("+ 30주선 위 비율 35% 미만·하락 중이면 미리 매도", m4 | ((pa < 35) & fall)),
             ("+ 신고가-신저가(4주 평균) -3% 미만이면 미리 매도", m4 | (nh4 < -3)),
             ("4단계라도 30주선 위 비율 50% 넘고 상승 중이면 다시 매수", m4 & ~((pa > 50) & rise)),
             ("4단계라도 30주선 위 비율 60% 넘으면 다시 매수", m4 & ~(pa > 60)),
             ("미리 매도(40%·하락) + 미리 매수(50%·상승)", (m4 & ~((pa > 50) & rise)) | (~m4 & (pa < 40) & fall)),
             ("미리 매도(신고가-신저가) + 미리 매수(50%·상승)", (m4 & ~((pa > 50) & rise)) | (~m4 & (nh4 < -3)))]
    rows = []
    qw = dfs["QQQ"]["Close"].reindex(cal, method="ffill") if "QQQ" in dfs else None
    for nm, out in rules:
        out = out.fillna(False).astype(bool)
        st = pd.Series(np.where(out, 4, 2), index=cal)
        sw = int((out != out.shift(1)).sum())
        if qw is not None:
            q = timed(qw / qw.iloc[0] * 100, st)
            rows.append(curve_row(f"[시장 폭] QQQ만 · {nm}", f"현금 기간 {out.mean() * 100:.0f}% · 바꾼 횟수 {sw}번", q))
            r_ = rows[-1]
            print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
                  f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
        if fill:
            e_, t_ = run_portfolio(base_run[0], frames, cal, ms0, max_pos=mp, fill=dict(fill, mkt=st), max_small=msmall, caps=capmap)
            rows.append(curve_row(f"[시장 폭] 전략(남는 돈 QQQ) · {nm}", f"QQQ 현금 기간 {out.mean() * 100:.0f}% · 거래 {len(t_)}건", e_["equity"]))
            r_ = rows[-1]
            print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
                  f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}%")
    return rows


def add_rows(frames, sig, line_n=10, strong=True):
    """보유 중 +5% 수익인 종목이 n주선(강한 종목: RS>0·n주선 상승 중)에 닿으면 추매. 손절 = 그 선 -5%. 원래 포지션과 함께 정리"""
    out = []
    for _, r in sig.iterrows():
        f = frames.get(r["ticker"])
        if f is None or r["entry_date"] not in f.index:
            continue
        j0 = f.index.get_loc(r["entry_date"])
        has_ex = pd.notna(r["exit_date"])
        ex = f.index.get_loc(r["exit_date"]) if has_ex and r["exit_date"] in f.index else len(f) - 1
        o, lo, c = f["open"].values, f["low"].values, f["close"].values
        line = f["close"].rolling(line_n).mean().values if line_n != 30 else f["ma30"].values
        rs = f["rs"].values
        for k in range(j0 + 2, ex):
            ln = line[k - 1]
            if math.isnan(ln) or c[k - 1] <= r["entry"] * 1.05 or c[k - 1] <= ln * 1.01:
                continue
            if strong and not (rs[k - 1] > 0 and line[k - 1] > line[k - 2]):
                continue
            if lo[k] <= ln * 1.01:
                px = min(o[k], ln * 1.01); st = ln * 0.95
                tr = _hold_from(f, k, px, st, True, None)
                xd, xp = tr[1], tr[3]
                if has_ex and (xd is None or xd > r["exit_date"]):
                    xd, xp = r["exit_date"], r["exit"]
                out.append(dict(ticker=r["ticker"], sig_date=f.index[k], entry_date=f.index[k], exit_date=xd, entry=px, exit=xp, stop0=st,
                                R=(xp - px) / (px - st), ret=(xp / px - 1) * 100 - 2 * BT["cost_pct"], weeks=0, why="추매",
                                score=r["score"], signal="추매", stage_label=r.get("stage_label"), dup=False, kind="ADD", add=True))
                break
    return pd.DataFrame(out)


def take_profit(frames, sig, pct):
    """수익이 pct% 나면 그 가격에 팔기 (원래 청산보다 먼저 닿으면)"""
    s2 = sig.copy()
    for i, r in s2.iterrows():
        f = frames.get(r["ticker"])
        if f is None or r["entry_date"] not in f.index:
            continue
        j0 = f.index.get_loc(r["entry_date"])
        ex = f.index.get_loc(r["exit_date"]) if pd.notna(r["exit_date"]) and r["exit_date"] in f.index else len(f) - 1
        tgt = r["entry"] * (1 + pct / 100)
        hi = f["high"].values
        for k in range(j0 + 1, ex + 1):
            if hi[k] >= tgt:
                px = max(tgt, f["open"].values[k])
                s2.at[i, "exit_date"], s2.at[i, "exit"] = f.index[k], px
                s2.at[i, "R"] = (px - r["entry"]) / (r["entry"] - r["stop0"]) if r["entry"] > r["stop0"] else 0
                s2.at[i, "ret"] = (px / r["entry"] - 1) * 100 - 2 * BT["cost_pct"]
                break
    return s2


def sector_rs_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap, secmap, buckets):
    """같은 업종(GICS 섹터) 안에서 RS 순위: 그 주 업종 내 RS 백분위, 같은 주 같은 업종 신호 중 RS 1등 여부"""
    sig0 = base_run[0]
    RS = pd.concat({t: f["rs"] for t, f in frames.items()}, axis=1).reindex(cal)
    pct = pd.DataFrame(index=cal, columns=RS.columns, dtype=float)
    by = {}
    for t in RS.columns:
        by.setdefault(secmap.get(t) or "기타", []).append(t)
    for sec, cols in by.items():
        if len(cols) >= 5:
            pct[cols] = RS[cols].rank(axis=1, pct=True)
    d = sig0[~sig0["dup"]].copy() if "dup" in sig0 else sig0.copy()
    d = d[d["ticker"].map(capmap) != "소형"]
    d["sec"] = d["ticker"].map(secmap).fillna("기타")
    d["pct"] = [pct[t].asof(dd) if t in pct else np.nan for t, dd in zip(d["ticker"], d["sig_date"])]
    d["rs_"] = [RS[t].asof(dd) if t in RS else np.nan for t, dd in zip(d["ticker"], d["sig_date"])]
    d["rank_wk"] = d.groupby(["sec", "sig_date"])["rs_"].rank(ascending=False, method="first")
    d["n_wk"] = d.groupby(["sec", "sig_date"])["rs_"].transform("count")
    hi = d["score"] >= ms0
    for lab, m in [("업종 내 RS 상위 10%", d["pct"] >= 0.9), ("업종 내 RS 상위 10~25%", (d["pct"] >= 0.75) & (d["pct"] < 0.9)),
                   ("업종 내 RS 25~50%", (d["pct"] >= 0.5) & (d["pct"] < 0.75)), ("업종 내 RS 하위 50%", d["pct"] < 0.5),
                   (f"{ms0}점+ · 업종 내 RS 상위 25%", hi & (d["pct"] >= 0.75)), (f"{ms0}점+ · 업종 내 RS 25~50%", hi & (d["pct"] >= 0.5) & (d["pct"] < 0.75)),
                   (f"{ms0}점+ · 업종 내 RS 하위 50%", hi & (d["pct"] < 0.5)),
                   ("같은 주 같은 업종 신호 2개+ 중 RS 1등", (d["n_wk"] >= 2) & (d["rank_wk"] == 1)),
                   ("같은 주 같은 업종 신호 2개+ 중 2등 이하", (d["n_wk"] >= 2) & (d["rank_wk"] > 1))]:
        st = stats_trades(d[m.fillna(False).astype(bool)])
        buckets.append((f"[업종 RS 순위] {lab} (대·중형)", st))
        print(f"  [업종 RS 순위] {lab}: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R · 3R+ {f_(st.get('big'))}% · 평균 수익 {f_(st.get('ret'))}%")
    rows = []
    s_all = sig0.copy()
    pa = np.array([pct[t].asof(dd) if t in pct else np.nan for t, dd in zip(s_all["ticker"], s_all["sig_date"])], dtype=float)
    for nm, fn in (("지금", None), ("업종 내 RS 상위 25%면 +5점", lambda sg: sg.assign(score=np.where(pa >= 0.75, sg["score"] + 5, sg["score"]))),
                   ("업종 내 RS 하위 50%면 사지 않음", lambda sg: sg[~(pa < 0.5)]),
                   ("같은 점수면 업종 내 RS 높은 순으로", lambda sg: sg.assign(rk=-np.nan_to_num(pa, nan=0.0)))):
        sg = s_all if fn is None else fn(s_all)
        e_, t_ = run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap, order="rk" if "rk" in sg else None)
        rows.append(curve_row(f"[업종 RS 순위] 포트폴리오 · {nm}", f"거래 {len(t_)}건", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}%")
    return rows


def climax_exit(frames, sig, thr, mode):
    """30주선 대비 thr% 넘게 오르면(과열): mode 'half' = 그 주 종가에 절반 매도·나머지는 원래 규칙 /
       'half10' = 절반 매도·나머지는 금요일 종가가 10주선 아래면 다음 주 시가 매도 / 'all10' = 전부 10주선 이탈 때 매도.
       절반 매도는 두 청산가를 반씩 섞은 값으로 근사 (먼저 판 돈의 재투자는 반영 안 함)"""
    s2 = sig.copy()
    n_hit = 0
    for i, r in s2.iterrows():
        f = frames.get(r["ticker"])
        if f is None or r["entry_date"] not in f.index:
            continue
        j0 = f.index.get_loc(r["entry_date"])
        has_ex = pd.notna(r["exit_date"]) and r["exit_date"] in f.index
        ex = f.index.get_loc(r["exit_date"]) if has_ex else len(f) - 1
        c, o, ma, ma10 = f["close"].values, f["open"].values, f["ma30"].values, f["close"].rolling(10).mean().values
        k = next((j for j in range(j0 + 1, ex) if not math.isnan(ma[j]) and c[j] / ma[j] - 1 >= thr / 100), None)
        if k is None:
            continue
        n_hit += 1
        p_half = c[k]
        p_rest, d_rest = r["exit"], r["exit_date"]
        if mode in ("half10", "all10"):
            j = next((j for j in range(k + 1, ex) if not math.isnan(ma10[j]) and c[j] < ma10[j]), None)
            if j is not None and j + 1 < len(f):
                p_rest, d_rest = o[j + 1], f.index[j + 1]
        px = p_rest if mode == "all10" else 0.5 * p_half + 0.5 * p_rest
        s2.at[i, "exit"], s2.at[i, "exit_date"] = px, d_rest
        s2.at[i, "R"] = (px - r["entry"]) / (r["entry"] - r["stop0"]) if r["entry"] > r["stop0"] else 0
        s2.at[i, "ret"] = (px / r["entry"] - 1) * 100 - 2 * BT["cost_pct"]
    return s2, n_hit


def climax_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap):
    sig0 = base_run[0]
    rows = []
    e_, t_ = run_portfolio(sig0, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
    rows.append(curve_row("[과열] 지금 (그냥 보유)", f"거래 {len(t_)}건", e_["equity"]))
    for thr in (50, 80, 100):
        for mode, nm in (("half", "절반 매도 · 나머지 원래대로"), ("half10", "절반 매도 · 나머지 10주선 이탈 때 매도"), ("all10", "매도 없이 손절만 10주선으로")):
            sg, nh = climax_exit(frames, sig0, thr, mode)
            e_, t_ = run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
            rows.append(curve_row(f"[과열] 30주선 +{thr}% 넘으면 {nm}", f"과열 도달 신호 {nh}건 · 거래 {len(t_)}건", e_["equity"]))
    for r_ in rows:
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    return rows


def addon_profit_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap):
    """추매(10주선·30주선)와 '조금 오르면 팔기'(익절)·갈아타기를 포트폴리오로 비교"""
    sig0 = base_run[0]
    rows = []
    def run(nm, sg, **kw):
        e_, t_ = run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap, **kw)
        na = int((t_["signal"] == "추매").sum()) if len(t_) and "signal" in t_ else 0
        rows.append(curve_row(nm, f"거래 {len(t_)}건 (추매 {na}건) · 승률 {f_(stats_trades(t_).get('win'))}%", e_["equity"]))
    run("[추매·익절] 지금 (추매 없음, 4단계·손절까지 보유)", sig0)
    a10 = add_rows(frames, sig0, 10, True)
    a30 = add_rows(frames, sig0, 30, False)
    print(f"  [추매·익절] 추매 후보 10주선 {len(a10)}건 · 30주선 {len(a30)}건")
    if len(a10):
        run("[추매·익절] + 10주선 추매 (수익 +5%·RS>0, 위험 절반)", pd.concat([sig0, a10], ignore_index=True))
    if len(a30):
        run("[추매·익절] + 30주선 추매 (수익 +5%, 위험 절반)", pd.concat([sig0, a30], ignore_index=True))
    for pct in (10, 20, 30, 50):
        run(f"[추매·익절] +{pct}% 오르면 팔기 (익절)", take_profit(frames, sig0, pct))
    run("[추매·익절] 더 좋은 신호가 오면 약한 보유 종목 팔고 갈아타기", sig0, rotate="weak", rot_min=85)
    run("[추매·익절] 25점 이상 높은 신호가 오면 갈아타기", sig0, rotate="gap", rot_min=85)
    for r_ in rows:
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    return rows


def touch10_portfolio_study(frames, cal, start, base_run, ms0, mp, fill, msmall, capmap):
    """10주선 터치 매수를 앱 신호에 더했을 때 포트폴리오 (대·중형 · 2단계 · RS 0+ · 30주선 위 · 시장 4단계 아님)"""
    sig0 = base_run[0]
    rows = []
    base = run_portfolio(sig0, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
    rows.append(curve_row("[10주선 포트폴리오] 지금 신호만", f"거래 {len(base[1])}건", base[0]["equity"]))
    for sp in (5.0, 8.0):
        mt = ma_touch_study(frames, start, capmap, n=10, stop_pct=sp)
        if mt.empty:
            continue
        ok = (~mt["sm"]) & (mt["st"] == 2) & (mt["rs"] >= 0) & (~mt["u30"]) & (mt["mkt"] != 4)
        t10 = mt[ok].copy()
        t10["signal"], t10["kind"], t10["dup"], t10["stage_label"] = "10주선", "T10", False, "2단계"
        print(f"  [10주선 포트폴리오] 손절 -{sp:.0f}% 조건 맞는 신호 {len(t10)}건 · 평균 {f_(stats_trades(t10).get('avgR'))}R")
        for nm, sc, only in (("지금 신호 + 10주선(점수 80, 같은 순위)", 80, False), ("지금 신호 + 10주선(점수 95, 먼저 삼)", 95, False),
                             ("지금 신호 + 10주선(점수 70, 남는 자리만)", ms0 - 0.5, False), ("10주선 신호만", 80, True)):
            t_ = t10.copy(); t_["score"] = sc
            cols = [c for c in sig0.columns if c in t_.columns]
            sg = t_[cols] if only else pd.concat([sig0, t_[cols]], ignore_index=True)
            mn = min(ms0, sc) if sc < ms0 else ms0
            e_, tr_ = run_portfolio(sg, frames, cal, mn, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
            k10 = int((tr_["signal"] == "10주선").sum()) if len(tr_) else 0
            rows.append(curve_row(f"[10주선 포트폴리오] 손절 -{sp:.0f}% · {nm}", f"거래 {len(tr_)}건 (10주선 {k10}건) · 승률 {f_(stats_trades(tr_).get('win'))}%", e_["equity"]))
    for r_ in rows:
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    return rows


def sizing_study(frames, cal, base_run, ms0, fill, msmall, capmap):
    """종목 수·한 번 손절 위험·한 종목 상한 조합별 결과. 반환: 곡선 표 행"""
    sig0 = base_run[0]
    rows = []
    old_r, old_w = BT["risk_pct"], BT["max_weight"]
    grid = [(6, 1.0, 25), (4, 1.0, 25), (5, 1.0, 25), (8, 1.0, 25), (10, 1.0, 25),
            (6, 1.5, 25), (6, 2.0, 25), (5, 1.5, 25), (4, 2.0, 25), (8, 1.5, 25), (6, 1.0, 20), (6, 1.5, 20)]
    for ms in (65, 70, 75, 80, 85):              # 개별주만(남는 돈 현금)일 때 매수 기준 점수
        e_, t_ = run_portfolio(sig0, frames, cal, ms, max_pos=6, fill=None, max_small=msmall, caps=capmap)
        rows.append(curve_row(f"[개별주만] {ms}점 이상 매수 · 남는 돈 현금", f"개별주 평균 {e_['stk'].mean():.0f}% · 거래 {len(t_)}건", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    try:
        for mp_, rk, mw in grid:
            BT["risk_pct"], BT["max_weight"] = rk, mw
            e_, t_ = run_portfolio(sig0, frames, cal, ms0, max_pos=mp_, fill=fill, max_small=msmall, caps=capmap)
            cap = min(mw, 100 / mp_)
            rows.append(curve_row(f"[비중] 최대 {mp_}종목 · 손절 위험 {rk:g}% · 한 종목 최대 {cap:.0f}%",
                                  f"평균 보유 {e_['n'].mean():.1f}종목 · 개별주 평균 {e_['stk'].mean():.0f}% · 거래 {len(t_)}건", e_["equity"]))
            r_ = rows[-1]
            print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
                  f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    finally:
        BT["risk_pct"], BT["max_weight"] = old_r, old_w
    return rows


def fill_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap, extra=()):
    """남는 돈 처리: QQQ로 채우기(지금) vs 현금으로 두기 vs 리밸런싱 폭을 넓혀 QQQ 매매 줄이기. 반환: 곡선 표 행"""
    sig0 = base_run[0]
    yrs = max((cal[-1] - cal[0]).days / 365.25, 1)
    rows = []
    vs = [("현금으로 둠 (개별주만)", None)]
    if fill:
        vs += [("QQQ로 채움 · 차이 10%면 맞춤", dict(fill, band=10)), ("QQQ로 채움 · 차이 25%면 맞춤 (지금)", fill),
               ("QQQ로 채움 · 4단계에도 안 팖", dict(fill, filt=False, band=25))] + list(extra)
    for nm, fl in vs:
        e_, t_ = run_portfolio(sig0, frames, cal, ms0, max_pos=mp, fill=fl, max_small=msmall, caps=capmap)
        tx = e_.attrs.get("etf_tx", 0)
        rows.append(curve_row(f"[남는 돈] {nm}", f"주식 평균 비중 {e_['stk'].mean():.0f}% · QQQ {e_['etf'].mean():.0f}% · QQQ 매매 연 {tx / yrs:.1f}회 · 개별주 거래 {len(t_)}건", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    return rows


def pause_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap):
    """손절이 연속으로 나면 몇 주 쉬기(또는 위험 절반)가 나은지. 반환: 곡선 표 행"""
    sig0 = base_run[0]
    rows = []
    vs = [("쉬지 않음 (지금)", None)] + [(f"{n}연속 손절 → {k}주 쉼", (n, k, "stop")) for n in (2, 3, 4) for k in (4, 8, 13)] + \
         [(f"{n}연속 손절 → {k}주 위험 절반", (n, k, "half")) for n, k in ((2, 8), (3, 8), (3, 13))]
    for nm, pz in vs:
        e_, t_ = run_portfolio(sig0, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap, pause=pz)
        rows.append(curve_row(f"[연속 손절] {nm}", f"쉰 횟수 {e_.attrs.get('n_pause', 0)}번 · 거래 {len(t_)}건 · 승률 {f_(stats_trades(t_).get('win'))}%", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · "
              f"2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}% · {r_['desc']}")
    # 손절이 연속으로 난 뒤 다음 거래의 성과 (실제 거래 순서)
    t0 = base_run[2].sort_values("entry_date") if len(base_run[2]) else None
    if t0 is not None and "exit_date" in t0:
        ex = t0.dropna(subset=["exit_date"]).sort_values("exit_date")
        for n in (1, 2, 3):
            nxt = []
            for _, r in t0.iterrows():
                prev = ex[ex["exit_date"] < r["entry_date"]].tail(n)
                if len(prev) == n and (prev["R"] <= 0).all():
                    nxt.append(r)
            st = stats_trades(pd.DataFrame(nxt)) if nxt else dict(n=0)
            print(f"  [연속 손절] 직전 {n}건이 모두 손절일 때 다음 거래: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R")
    return rows


def pullback_quality_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap, buckets):
    """풀백 신호: RS가 떨어지는 중·고점이 낮아지는 중이면 성과가 나쁜지, 감점하면 포트폴리오가 나아지는지"""
    for f in frames.values():
        if "rs13d" not in f:
            f["rs13d"] = f["rs"] - f["rs"].shift(13)
            f["rs_off"] = f["rs"] - f["rs"].rolling(26, min_periods=8).max()
            hi = f["high"]
            f["lh_big"] = (hi.rolling(13).max() < hi.shift(13).rolling(26).max() * 0.97).astype(float)   # 최근 13주 고점이 그 전 26주 고점보다 3%+ 낮음
    sig0 = base_run[0]
    def at(col, df):
        return np.array([frames[t][col].asof(d) for t, d in zip(df["ticker"], df["sig_date"])], dtype=float)
    a = sig0[~sig0["dup"]].copy() if "dup" in sig0 else sig0.copy()
    a = a[(a["signal"] == "풀백") & (a["ticker"].map(capmap) != "소형")]
    if a.empty:
        return []
    a["rs13d"], a["rs_off"], a["lh"], a["lhb"], a["rs_"] = at("rs13d", a), at("rs_off", a), at("lh", a) if "lh" in next(iter(frames.values())) else np.nan, at("lh_big", a), at("rs", a)
    fall = (a["rs13d"] <= -10)
    off = (a["rs_off"] <= -20)
    for lab, m in [("전체", a["rs13d"].notna()), ("RS 13주 동안 10 이상 하락", fall), ("RS 13주 변화 -10~0", (a["rs13d"] > -10) & (a["rs13d"] <= 0)), ("RS 13주 상승", a["rs13d"] > 0),
                   ("RS가 26주 최고보다 20 이상 아래", off), ("RS 26주 최고 근처(-20 이내)", a["rs_off"] > -20),
                   ("작은 고점이 낮아짐(LH)", a["lh"] == 1), ("큰 흐름 고점 낮아짐(13주 고점 < 그 전 26주 고점 -3%)", a["lhb"] == 1), ("큰 흐름 고점 낮아지지 않음", a["lhb"] == 0),
                   ("RS 하락 + 큰 흐름 고점 낮아짐 (KRYS 유형)", fall & (a["lhb"] == 1)), ("둘 다 아님", ~fall & (a["lhb"] == 0))]:
        st = stats_trades(a[m.fillna(False).astype(bool)])
        buckets.append((f"[풀백 품질] {lab} (대·중형, {ms0}점 이상 포함 전체 신호)", st))
        print(f"  [풀백 품질] {lab}: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R · 3R+ {f_(st.get('big'))}%")
    rows = []
    s_all = sig0.copy()
    pb = (s_all["signal"] == "풀백")
    rsd, lhb = at("rs13d", s_all), at("lh_big", s_all)
    for nm, cond, pts in (("감점 없음 (지금)", None, 0), ("RS 13주 -10 이상 하락 풀백 -8", rsd <= -10, 8), ("큰 흐름 고점 낮아진 풀백 -8", lhb == 1, 8),
                          ("RS 하락 + 고점 낮아짐 풀백 -10", (rsd <= -10) & (lhb == 1), 10)):
        sg = s_all.copy()
        if cond is not None:
            sg.loc[pb & cond, "score"] = sg.loc[pb & cond, "score"] - pts
        e_, t_ = run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
        rows.append(curve_row(f"[풀백 품질] {nm}", f"거래 {len(t_)}건", e_["equity"]))
        r_ = rows[-1]
        print(f"  {r_['name']}: 연평균 {f_(r_['c'].get('cagr'))}% · 최대낙폭 {f_(r_['c'].get('mdd'))}% · ~2015 {f_(r_['a'].get('cagr'))}% · 2016~ {f_(r_['b'].get('cagr'))}% · 2008 {f_(r_['y08'])}% · 2022 {f_(r_['y22'])}%")
    return rows


def vol2_study(allsig, frames, capmap, buckets):
    """늦은 요일 돌파는 주봉 거래량이 작게 보일 수 있음 → 돌파 주 + 다음 주 거래량(돌파 전 10주 평균 대비)으로 다시 보기"""
    d = allsig[allsig["kind"].isin(["B12", "B2"]) & (allsig["ticker"].map(capmap) != "소형")].copy()
    v1, v2 = [], []
    for t, dd in zip(d["ticker"], d["entry_date"]):
        f = frames[t]
        try:
            i = f.index.get_loc(dd)
        except KeyError:
            v1.append(np.nan); v2.append(np.nan); continue
        avg = f["vavg"].iloc[i]
        if not avg or np.isnan(avg) or i + 1 >= len(f):
            v1.append(np.nan); v2.append(np.nan); continue
        v1.append(f["vol"].iloc[i] / avg); v2.append((f["vol"].iloc[i] + f["vol"].iloc[i + 1]) / (2 * avg))
    d["v1"], d["v2"] = v1, v2
    for lab, m in [("돌파 주 2배+", d["v1"] >= 2), ("돌파 주 2배 미만", d["v1"] < 2),
                   ("돌파 주 2배 미만 → 2주 평균 2배+ (늦게 확인)", (d["v1"] < 2) & (d["v2"] >= 2)),
                   ("돌파 주 2배 미만 → 2주 평균 1.5~2배", (d["v1"] < 2) & (d["v2"] >= 1.5) & (d["v2"] < 2)),
                   ("돌파 주 2배 미만 → 2주 평균도 1.5배 미만", (d["v1"] < 2) & (d["v2"] < 1.5)),
                   ("2주 평균 2배+ (돌파 주 여부 무관)", d["v2"] >= 2), ("2주 평균 2배 미만", d["v2"] < 2)]:
        st = stats_trades(d[m.fillna(False)])
        buckets.append((f"[2주 거래량] {lab} (대·중형 돌파)", st))
        print(f"  [2주 거래량] {lab}: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R · 3R+ {f_(st.get('big'))}%")


def universe_study(frames, cal, base_run, ms0, mp, fill, msmall, capmap, srcmap):
    """종목 범위별 같은 전략: 원래 지수 목록만 / + 해외 ADR / + 나스닥 시총 2조원+ 추가 종목. 반환: 곡선 표 행"""
    sig0, eq0, _ = base_run
    src = sig0["ticker"].map(srcmap).fillna("mine")
    base = src.isin(["sp500", "ndx", "sp400", "r2000", "mine"])
    adr = src.isin(["adr", "adr_mid"])
    nas = src == "nasdaq"
    rows = []
    for nm, m in (("원래 지수 목록만 (S&P500·400·나스닥100·러셀2000)", base), ("원래 + 해외 ADR", base | adr),
                  ("원래 + 나스닥 시총 2조원+ 추가", base | nas), ("전체 (지금 앱)", base | adr | nas)):
        e_, t_ = run_portfolio(sig0[m], frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
        rows.append(curve_row(f"[종목 범위] {nm}", f"같은 규칙 · 거래 {len(t_)}건", e_["equity"]))
    cl = sig0[~sig0["dup"]] if "dup" in sig0 else sig0
    cs = cl["ticker"].map(srcmap).fillna("mine")
    for nm, m in (("원래 지수", cs.isin(["sp500", "ndx", "sp400", "r2000", "mine"])), ("해외 ADR", cs.isin(["adr", "adr_mid"])), ("나스닥 추가", cs == "nasdaq")):
        st = stats_trades(cl[m & (cl["score"] >= ms0)])
        print(f"  [종목 범위] {nm} 종목의 {ms0}점+ 신호: {st.get('n', 0)}건 · 승률 {f_(st.get('win'))}% · 평균 {f_(st.get('avgR'))}R")
    for r in rows:
        print(f"  {r['name']}: 연평균 {f_(r['c'].get('cagr'))}% · 최대낙폭 {f_(r['c'].get('mdd'))}% · 앞 {f_(r['a'].get('cagr'))}% · 뒤 {f_(r['b'].get('cagr'))}% · {r['desc']}")
    return rows


def crash_study(frames, start, capmap, earn):
    """2단계 종목이 한 주에 -10% 이상 급락(거래량 1.5배+)한 뒤: 그 주 종가가 30주선을 지켰는지에 따라 이후 수익률.
       실적 발표 주(그 주 안에 실적일) 여부도 나눔. 기준선: 2단계 종목의 아무 주."""
    ev, base = [], []
    hz = (4, 13, 26, 52)
    for t, f in frames.items():
        if capmap.get(t) == "소형":
            continue
        c, ma, st, vr, idx = f["close"].values, f["ma30"].values, f["stage"].values, f["vr"].values, f.index
        n = len(f)
        ds = earn.get(t)
        last = -99
        for i in range(1, n - 1):
            if idx[i] < start or st[i - 1] != 2 or math.isnan(ma[i]):
                continue
            wk = c[i] / c[i - 1] - 1
            fwd = [(c[i + h] / c[i] - 1) * 100 if i + h < n else np.nan for h in hz]
            if i % 4 == 0:
                base.append(fwd)
            if wk > -0.10 or (vr[i] if not math.isnan(vr[i]) else 0) < 1.5 or i - last < 13:
                continue
            last = i
            held = c[i] >= ma[i]
            e = np.nan
            if ds is not None and len(ds):
                k = ds.searchsorted(idx[i] - pd.Timedelta(days=8))
                e = float(k < len(ds) and ds[k] <= idx[i])
            brk13 = bool((c[i + 1:min(n, i + 14)] < ma[i + 1:min(n, i + 14)] * 0.97).any())
            ev.append(dict(held=held, earn=e, drop=wk * 100, brk13=brk13, cap=capmap.get(t), **{f"r{h}": v for h, v in zip(hz, fwd)}))
    ev = pd.DataFrame(ev)
    b = pd.DataFrame(base, columns=[f"r{h}" for h in hz])
    def line(lab, d):
        if not len(d):
            print(f"  [급락 후] {lab}: 0건"); return
        med = " · ".join(f"{h}주 {d[f'r{h}'].median():+.1f}%" for h in hz)
        win = (d["r26"] > 0).mean() * 100
        extra = f" · 13주 안 30주선 -3% 이탈 {d['brk13'].mean() * 100:.0f}%" if "brk13" in d else ""
        print(f"  [급락 후] {lab}: {len(d)}건 · 중앙값 {med} · 26주 뒤 오른 비율 {win:.0f}%{extra}")
    line("기준: 2단계 대·중형주 아무 주", b)
    if len(ev):
        line("2단계 대·중형 주간 -10%+ 급락 (거래량 1.5배+) 전체", ev)
        line("· 그 주 종가가 30주선 위 (지킴)", ev[ev["held"]])
        line("· 그 주 종가가 30주선 아래 (깨짐)", ev[~ev["held"]])
        line("· 실적 발표 주 급락 · 30주선 지킴", ev[(ev["earn"] == 1) & ev["held"]])
        line("· 실적 발표 주 급락 · 30주선 깨짐", ev[(ev["earn"] == 1) & ~ev["held"]])
        line("· 실적 아닌 급락 · 30주선 지킴", ev[(ev["earn"] == 0) & ev["held"]])
        line("· 30주선 지킴 · 급락 -10~-15%", ev[ev["held"] & (ev["drop"] > -15)])
        line("· 30주선 지킴 · 급락 -15% 이상", ev[ev["held"] & (ev["drop"] <= -15)])
        h_ = ev[ev["held"]]
        line("· 30주선 지킴 · 이후 13주 안 30주선 안 깨짐", h_[~h_["brk13"]])
        line("· 30주선 지킴 · 이후 13주 안 30주선 깨짐", h_[h_["brk13"]])


def pattern_flags(f, k=3):
    """주봉 패턴 (그 주까지 확인된 고점·저점만 사용 → 미래 정보 없음). 고점·저점 = 앞뒤 k주 중 최고·최저 (k주 뒤에 확정)
       hhhl: 최근 고점 2개·저점 2개가 모두 높아짐 / lh: 최근 고점 2개가 낮아짐
       dbl / tpl: 이중·삼중 바닥 넥라인(바닥 사이 고점) 종가 돌파한 주 / tl: 낮아지는 고점 2개를 이은 하락 추세선을 종가로 돌파한 주"""
    h, l, c = f["high"].values, f["low"].values, f["close"].values
    n = len(f)
    hhhl, lh, dbl, tpl, tl = (np.zeros(n) for _ in range(5))
    ph, pl = [], []           # 확정된 (위치, 값)
    used_dbl, used_tl = -1, -1
    for j in range(2 * k + 1, n):
        i = j - k               # 이번 주에 확정되는 후보
        w = slice(i - k, i + k + 1)
        if h[i] == np.nanmax(h[w]):
            ph.append((i, h[i]))
        if l[i] == np.nanmin(l[w]):
            pl.append((i, l[i]))
        if len(ph) >= 2 and len(pl) >= 2:
            hhhl[j] = float(ph[-1][1] > ph[-2][1] and pl[-1][1] > pl[-2][1])
        if len(ph) >= 2:
            lh[j] = float(ph[-1][1] < ph[-2][1])
            (i1, v1), (i2, v2) = ph[-2], ph[-1]
            if v2 < v1 and i2 > i1 and i2 > used_tl:
                line = lambda x: v1 + (v2 - v1) * (x - i1) / (i2 - i1)
                if c[j] > line(j) and c[j - 1] <= line(j - 1):
                    tl[j] = 1.0; used_tl = i2
        if len(pl) >= 2:
            (a1, b1), (a2, b2) = pl[-2], pl[-1]
            if a2 - a1 >= 4 and abs(b2 / b1 - 1) <= 0.03 and a2 > used_dbl:
                mids = [v for x, v in ph if a1 < x < a2]
                if mids and c[j] > max(mids) and c[j - 1] <= max(mids):
                    if len(pl) >= 3 and pl[-2][0] - pl[-3][0] >= 4 and abs(pl[-3][1] / b1 - 1) <= 0.04:
                        tpl[j] = 1.0
                    else:
                        dbl[j] = 1.0
                    used_dbl = a2
    f["hhhl"], f["lh"], f["dbl"], f["tpl"], f["tl"] = hhhl, lh, dbl, tpl, tl
    for col in ("dbl", "tpl", "tl"):
        f[col + "13"] = pd.Series(f[col].values, index=f.index).rolling(13, min_periods=1).max().values


def pattern_study(frames, start, capmap):
    """패턴이 완성된(돌파한) 주 종가에 샀다면 이후 수익률 (대·중형). 기준: 아무 주"""
    hz = (13, 26, 52)
    ev = {}
    base = []
    for t, f in frames.items():
        if capmap.get(t) == "소형":
            continue
        if "dbl" not in f:
            pattern_flags(f)
        c, ma, st, idx = f["close"].values, f["ma30"].values, f["stage"].values, f.index
        n = len(f)
        cols = {"이중 바닥 넥라인 돌파": f["dbl"].values, "삼중 바닥 넥라인 돌파": f["tpl"].values, "하락 추세선 돌파": f["tl"].values,
                "고점·저점 높아지기 시작 (HH·HL)": f["hhhl"].values, "고점 낮아지기 시작 (LH)": f["lh"].values,
                "역헤드앤숄더 돌파": f["ihs"].values if "ihs" in f else np.zeros(n), "헤드앤숄더 천장 완성": f["hs_top"].values if "hs_top" in f else np.zeros(n)}
        prev = {k: 0 for k in cols}
        for i in range(1, n):
            if idx[i] < start:
                continue
            fwd = [(c[i + h] / c[i] - 1) * 100 if i + h < n else np.nan for h in hz]
            if i % 4 == 0:
                base.append(fwd)
            for nm, a in cols.items():
                v = a[i] if not (isinstance(a[i], float) and math.isnan(a[i])) else 0
                if v > 0 and prev[nm] <= 0:
                    above = (not math.isnan(ma[i])) and c[i] > ma[i]
                    ev.setdefault(nm, []).append(fwd + [above, st[i]])
                prev[nm] = v
    def line(lab, rows):
        if not len(rows):
            print(f"  [패턴] {lab}: 0건"); return
        d = np.array([r[:3] for r in rows], float)
        med = " · ".join(f"{h}주 {np.nanmedian(d[:, j]):+.1f}%" for j, h in enumerate(hz))
        print(f"  [패턴] {lab}: {len(rows)}건 · 중앙값 {med} · 26주 뒤 오른 비율 {np.nanmean(d[:, 1] > 0) * 100:.0f}%")
    line("기준: 대·중형주 아무 주", base)
    for nm, rows in ev.items():
        line(nm, rows)
        line(f"· {nm} · 30주선 위", [r for r in rows if r[3]])
        line(f"· {nm} · 30주선 아래", [r for r in rows if not r[3]])


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


def _hold_mh(f, j0, e, s0, major):
    """잔고점 돌파 보유: j0주 중간 매수. 초기 손절 · 전고점(major) -3%까지 갔다가 꺾이면(주봉 종가가 전주 저가 아래 & 전고점 아래) 다음 주 시가 매도 ·
       전고점을 종가로 넘으면 일반 보유(4단계까지)로 전환"""
    o, lo, c, stg = f["open"].values, f["low"].values, f["close"].values, f["stage"].values
    risk = e - s0
    if c[j0] < s0:
        return (f.index[j0], f.index[j0], e, s0, -1.0, 0, "손절", s0)
    touched, freed = False, False
    j = j0 + 1
    while j < len(f):
        if lo[j] <= s0:
            px = min(s0, o[j])
            return (f.index[j0], f.index[j], e, px, (px - e) / risk, j - j0, "손절", s0)
        if stg[j] == 4 and j + 1 < len(f):
            px = o[j + 1]
            return (f.index[j0], f.index[j + 1], e, px, (px - e) / risk, j - j0 + 1, "4단계 진입", s0)
        if not freed:
            if c[j] > major:
                freed = True
            elif f["high"].values[j] >= major * 0.97:
                touched = True
            if touched and not freed and c[j] < lo[j - 1] and c[j] < major and j + 1 < len(f):
                px = o[j + 1]
                return (f.index[j0], f.index[j + 1], e, px, (px - e) / risk, j - j0 + 1, "전고점 앞 꺾임", s0)
        j += 1
    return (f.index[j0], None, e, c[-1], (c[-1] - e) / risk, len(f) - 1 - j0, "보유 중", s_orig)


def entry_study(frames, bsig):
    """돌파 이벤트마다: 돌파 100% / 돌파 50%+되돌림 50% / 되돌림 100%, 그리고 10주선·30주선 추매 성적"""
    out = {k: [] for k in ("A", "B", "C", "Cfill", "add10", "add30")}
    for _, r in bsig.iterrows():
        f = frames[r["ticker"]]
        j0 = f.index.get_loc(r["entry_date"])
        res = f["res"].values[j0]
        if math.isnan(res):
            continue
        o, lo, c, ma = f["open"].values, f["low"].values, f["close"].values, f["ma30"].values
        stop = r["stop0"]
        ra = r["ret"]
        # 되돌림 매수: 13주 안에 돌파선 +3%까지 내려오면 그 가격에 (손절은 돌파 매수와 같은 가격)
        P = res * 1.03
        rc, rrc = None, None
        for k in range(j0 + 1, min(j0 + 14, len(f))):
            if lo[k] <= P:
                px = min(o[k], P)
                if px <= stop * 1.01:
                    break
                tr = _hold_from(f, k, px, stop, True, None)
                rc = (tr[3] / px - 1) * 100 - 2 * BT["cost_pct"]; rrc = tr[4]
                break
        out["A"].append((ra, r["R"]))
        out["Cfill"].append(rc is not None)
        out["C"].append((rc if rc is not None else 0.0, rrc if rrc is not None else 0.0))
        out["B"].append((0.5 * ra + 0.5 * (rc or 0.0), 0.5 * r["R"] + 0.5 * (rrc or 0.0)))
        # 추매: 수익 중(+5%)인 종목이 10주선(RS>0·10주선 상승 중인 강한 종목만) 또는 30주선에 닿을 때
        has_ex = pd.notna(r["exit_date"])
        ex = f.index.get_loc(r["exit_date"]) if has_ex else len(f) - 1
        ma10 = f["close"].rolling(10).mean().values
        rs = f["rs"].values
        for key, line, strong in (("add10", ma10, True), ("add30", ma, False)):
            for k in range(j0 + 2, ex):
                ln = line[k - 1]
                if math.isnan(ln) or c[k - 1] <= e_pos(r) * 1.05 or c[k - 1] <= ln * 1.01:
                    continue
                if strong and not (rs[k - 1] > 0 and ma10[k - 1] > ma10[k - 2]):
                    continue
                if lo[k] <= ln * 1.01:
                    px = min(o[k], ln * 1.01)
                    st = ln * 0.95
                    tr = _hold_from(f, k, px, st, True, None)
                    xd = tr[1] if tr[1] is not None else f.index[-1]
                    if has_ex and xd > r["exit_date"]:      # 원래 포지션과 함께 정리
                        xp = r["exit"]; R_ = (xp - px) / (px - st)
                        out[key].append(((xp / px - 1) * 100 - 2 * BT["cost_pct"], R_))
                    else:
                        out[key].append(((tr[3] / px - 1) * 100 - 2 * BT["cost_pct"], tr[4]))
                    break
    return out


EXT_BINS = [(-8, -5), (-5, -3), (-3, 0), (0, 3), (3, 5), (5, 8), (8, 12), (12, 16), (16, 20)]


def ext_study(frames, start, capmap, score_col="score2TPt", min_sc=60):
    """2단계·30주선 상승 중인 종목을 30주선 이격 구간별로 다음 주 시가에 샀다면 (손절: 3주 저점 -1%·30주선 -2% 중 낮은 쪽, 4단계까지 보유)"""
    res = {b: [] for b in EXT_BINS}
    for t, f in frames.items():
        if capmap.get(t) == "소형":
            continue
        st, sl, ext, ma, low3, o = f["stage"].values, f["slope"].values, f["ext"].values, f["ma30"].values, f["low3"].values, f["open"].values
        sc = f[score_col].values
        ok = f.index >= start
        last = {b: -99 for b in EXT_BINS}
        for i in range(30, len(f) - 1):
            if not ok[i] or st[i] != 2 or not (sl[i] > S.CFG["flat_pct"]) or not (sc[i] >= min_sc):
                continue
            for b in EXT_BINS:
                if b[0] <= ext[i] < b[1]:
                    if i - last[b] <= 8:
                        break
                    last[b] = i
                    e = o[i + 1]
                    s0 = min(low3[i] * 0.99, ma[i] * 0.98, e * 0.97)
                    if (e - s0) / e * 100 > 25:
                        break
                    tr = _hold_from(f, i + 1, e, s0, False, None)
                    res[b].append(((tr[3] / e - 1) * 100 - 2 * BT["cost_pct"], tr[4], (e - s0) / e * 100))
                    break
    return res


def _open_near(o, d, days=3):
    """d 주의 시가. 종목마다 주봉 날짜가 하루쯤 어긋날 수 있어 ±days 안에서 가장 가까운 주를 씀"""
    v = o.reindex([d]).iloc[0]
    if pd.notna(v):
        return v
    w = o[(o.index >= d - pd.Timedelta(days=days)) & (o.index <= d + pd.Timedelta(days=days))].dropna()
    return w.iloc[0] if len(w) else np.nan


def etf_timing(dfs, tickers, cal, maxn=1, enter=(2,), exit_=(4,), rank="rs", bench=None):
    """ETF를 와인스테인식으로: 지난주 단계가 enter면 이번 주 시가에 매수(빈자리·RS 순), exit면 매도. 같은 비중"""
    cost = BT["cost_pct"] / 100
    info = {}
    for t in tickers:
        if t not in dfs:
            continue
        f = S.indicators(dfs[t])
        st = f.apply(lambda r: (S.classify(r) or (None,))[0], axis=1)
        rs = S.mansfield_series(f["close"], bench) if bench is not None else pd.Series(0.0, index=f.index)
        info[t] = dict(o=f["open"], c=f["close"], st=st, rs=rs)
    cash, pos = 100.0, {}
    curve, trades, wk_in = [], 0, 0
    for d in cal:
        dp = d - pd.Timedelta(days=1)
        prev = {t: (v["st"].asof(dp), v["rs"].asof(dp)) for t, v in info.items()}
        # 매도
        for t in list(pos):
            if prev[t][0] in exit_:
                px = info[t]["o"].asof(d)
                cash += pos.pop(t) * px * (1 - cost); trades += 1
        # 매수
        eq = cash + sum(n * info[t]["c"].asof(d) for t, n in pos.items() if pd.notna(info[t]["c"].asof(d)))
        cands = [t for t in info if t not in pos and prev[t][0] in enter and pd.notna(_open_near(info[t]["o"], d))]
        cands.sort(key=lambda t: -(prev[t][1] if pd.notna(prev[t][1]) else -99))
        for t in cands:
            if len(pos) >= maxn:
                break
            px = _open_near(info[t]["o"], d)
            val = min(eq / maxn, cash / (1 + cost))
            if val <= eq * 0.01:
                break
            pos[t] = val / px; cash -= val * (1 + cost); trades += 1
        mv = sum(n * info[t]["c"].asof(d) for t, n in pos.items())
        curve.append((d, cash + mv))
        wk_in += 1 if pos else 0
    e = pd.Series([x[1] for x in curve], index=[x[0] for x in curve])
    return e, trades, wk_in / max(1, len(cal)) * 100


def fetch_earnings(tickers):
    """야후에서 과거 실적 발표 날짜 (종목당 최대 60개 ≈ 15년). 실패한 종목은 빠짐"""
    import concurrent.futures as cf
    try:
        import yfinance as yf
    except Exception:
        return {}
    def one(t):
        try:
            d = yf.Ticker(t).get_earnings_dates(limit=60)
            if d is None or len(d) == 0:
                return t, None
            idx = pd.DatetimeIndex(d.index).tz_localize(None).normalize().sort_values()
            return t, idx
        except Exception:
            return t, None
    out = {}
    print(f"실적 발표 날짜 받는 중 ({len(tickers)}종목)...")
    with cf.ThreadPoolExecutor(8) as ex:
        for t, idx in ex.map(one, tickers):
            if idx is not None:
                out[t] = idx
    print(f"  {len(out)}종목 받음")
    return out


def e_pos(r):
    return r["entry"]


def collect_variant(frames, start, vol_rule, entry_rule, min_score_floor=50, pb_rule="near", pb_bonus=0, small=None, caps=None, score_col="score2",
                    rt_stop=False, b12=0, only12=False, mh=None, block_rebreak=0, be=None, pb_stop="mix", stop_mode="intra", emerg=None,
                    vol_need=None, min_base=0, max_gap=None, ma_exit=None, chand=None):
    """vol_need: 돌파 매수한 주 거래량이 이 배수 미만이면 다음 주 시가 매도 · min_base: 기저가 이 주 수 미만인 돌파는 안 삼
       max_gap: 시가가 매수 스톱 가격보다 이 % 넘게 위에서 시작하면 그 돌파는 건너뜀"""
    """mh: 잔고점 돌파 dict(stop='pct'|'low3', bonus=점수 가점, k=최근 몇 주 고점)
       block_rebreak: 돌파 매수가 손절로 끝난 뒤 이 주 수 동안 같은 종목 돌파 재매수 안 함"""
    """rt_stop: 돌파선 되돌림 풀백의 손절을 '돌파선 -3%·3주 저점 -1% 중 낮은 쪽'으로
       b12: 1→2단계 돌파(1단계였거나, 기저 15주+ 뒤 30주선이 막 오르기 시작) 가점 · only12: 그 외 돌파는 안 함"""
    """small: 소형주 전용 규칙 dict(out=빼기, dv=주간 거래대금 하한, atr=ATR×2 손절, vol2=돌파 주 거래량 2배, sec2=업종 2단계만, nopb=풀백 제외)"""
    small = small or {}
    caps = caps or {}
    """pb_rule: 'near'(30주선 근처면 매수) | 'bounce'(근처에서 거래량 늘며 반등한 주 다음 주 시가) |
       'quiet'(bounce + 눌림 동안 거래량 감소) | 'bstop'(bounce 주 고가 +0.5% 위로 올라갈 때 매수) | 'none'(풀백 안 함)"""
    """vol_rule: 'base'(돌파 1.5배) | 'strict'(돌파 2배·풀백은 거래량 감소)
       entry_rule: 'monday'(신호 다음 주 시가) | 'stop'(저항선 +0.5% 매수 스톱) | 'stopvol'(매수 스톱 + 그 주 거래량 2배 미만이면 다음 주 매도)"""
    pct = S.CFG["breakout_stop_pct"]
    rows = []
    for t, f in frames.items():
        sm = small if caps.get(t) == "소형" else {}
        if sm.get("out"):
            continue
        sc, sg = f[score_col].values, f["signal2"].values
        cprev = f["close"].shift(1)
        atr = pd.concat([f["high"] - f["low"], (f["high"] - cprev).abs(), (f["low"] - cprev).abs()], axis=1).max(axis=1).rolling(10).mean().values
        dvol, secst = f["dollar_vol"].values, f["sec_stage"].values
        bl_, extv, blen, upw = f["brk_level"].values, f["ext"].values, f["base_len"].values, f["ma_up_weeks"].values
        lowv = f["low"].values
        last_bk_stop = -999           # 마지막 돌파 손절이 난 주
        last_bk_exit = None
        rk = sm["rk"][t].reindex(f.index).values if sm.get("top") and t in sm["rk"] else None

        def widen(s0, e, i):          # 소형주 ATR 손절: 기존 손절과 '매수가 - ATR×2' 중 낮은 쪽
            if sm.get("atr") and not math.isnan(atr[i]):
                return min(s0, e - 2 * atr[i])
            return s0
        o, h, c, res, vr, bvr = f["open"].values, f["high"].values, f["close"].values, f["res"].values, f["vr"].values, f["brk_vr"].values
        st, slope, ma, low3 = f["stage"].values, f["slope"].values, f["ma30"].values, f["low3"].values
        idx_ok = f.index >= start
        last_entry = -99
        for i in range(30, len(f) - 1):
            if not idx_ok[i] or not (sc[i] >= min_score_floor):
                continue
            if sm.get("dv") and not (dvol[i] >= sm["dv"]):
                continue
            if sm.get("top") and not (rk is not None and rk[i] <= sm["top"]):     # 그 주 소형주 중 거래대금 순위 (지난 10주 평균)
                continue
            if sm.get("sec2") and secst[i] != 2:
                continue
            g = sg[i]
            if sm.get("nopb") and g == "풀백":
                g = ""
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
                kind = "MA" if -3 <= extv[i] <= 5 else "RT"          # 30주선 풀백 / 돌파선 되돌림
                if kind == "RT" and rt_stop and not math.isnan(bl_[i]):
                    s0 = min(min(low3[i] * 0.99, bl_[i] * 0.97), e * (1 - BT["min_stop_pct"] / 100))
                elif pb_stop in ("ma2", "ma3", "ma5", "maclose"):
                    pctm = {"ma2": 0.98, "ma3": 0.97, "ma5": 0.95, "maclose": 0.97}[pb_stop]
                    s0 = min(ma[i] * pctm, e * (1 - BT["min_stop_pct"] / 100))
                else:
                    s0 = min(min(low3[i] * 0.99, ma[i] * 0.98), e * (1 - BT["min_stop_pct"] / 100))
                s0 = widen(s0, e, i)
                if not (e > s0 > 0) or (e - s0) / e * 100 > BT["max_stop_pct"]:
                    continue
                tr = _hold_from(f, i + 1, e, s0, False, None, be, stop_mode, pb_stop == "maclose", emerg, ma_exit, chand)
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
                bon = pb_bonus if (g == "풀백" and pb_bonus and _bounce(f, i, False)) else 0
                rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                 R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why, score=min(100.0, float(sc[i]) + bon),
                                 signal=g, stage_label=f["stage_label"].iloc[i], dup=False, kind=kind if g == "풀백" else "B"))
            # 잔고점 돌파: 30주선 우상향 · 종가가 30주선 위 · 26주 고점(전고점)보다 5% 이상 낮은 최근 k주 고점을 넘을 때 매수 스톱
            if mh and i + 1 - last_entry > 4 and st[i] == 2 and slope[i] > S.CFG["flat_pct"] and c[i] > ma[i]:
                j = i + 1
                k = mh.get("k", 8)
                mhi = h[i - k + 1:i + 1].max()
                major = res[j]
                if not math.isnan(major) and mhi < major * 0.95 and mhi * 0.93 <= c[i] < mhi:
                    trig = mhi * 1.005
                    if h[j] >= trig and o[j] <= mhi * 1.05:
                        e = max(o[j], trig)
                        s0 = e * 0.95 if mh.get("stop") == "pct" else min(low3[i] * 0.99, e * 0.97)
                        if (e - s0) / e * 100 <= 12:
                            tr = _hold_mh(f, j, e, s0, major)
                            last_entry = j
                            ent, ex, e, px, R, wk, why, s0 = tr
                            rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                             R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why,
                                             score=min(100.0, float(sc[i]) + mh.get("bonus", 8)),
                                             signal="돌파", stage_label=f["stage_label"].iloc[i], dup=False, kind="MH", rebreak=False))
                            continue
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
                is12 = st[i] == 1 or (blen[i] >= 15 and upw[i] <= 8)
                if only12 and not is12:
                    continue
                rebreak = (j - last_bk_stop) <= 13
                if block_rebreak and (j - last_bk_stop) <= block_rebreak:
                    continue
                if min_base and not (blen[i] >= min_base):
                    continue
                if max_gap is not None and o[j] > trig * (1 + max_gap / 100):
                    continue
                e = max(o[j], trig)
                s0 = widen(e * (1 - pct / 100), e, i)
                if (e - s0) / e * 100 > BT["max_stop_pct"]:
                    continue
                tr = _hold_from(f, j, e, s0, True, 2.0 if (entry_rule == "stopvol" or sm.get("vol2")) else vol_need, be, stop_mode, False, emerg, ma_exit, chand)
                gapk = "up" if o[j] >= trig else ("down" if o[j] <= c[i] * 0.98 else "normal")
                last_entry = j
                ent, ex, e, px, R, wk, why, s0 = tr
                rows.append(dict(ticker=t, sig_date=f.index[i], entry_date=ent, exit_date=ex, entry=e, exit=px, stop0=s0,
                                 R=R, ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=wk, why=why,
                                 score=min(100.0, float(sc[i]) + (b12 if is12 else 0)),
                                 signal="돌파", stage_label=f["stage_label"].iloc[i], dup=False, kind="B12" if is12 else "B2", rebreak=rebreak,
                                 gap=gapk, gap_pct=(o[j] / trig - 1) * 100))
                if why == "손절" and ex is not None:
                    last_bk_stop = f.index.get_loc(ex)
    return pd.DataFrame(rows)


# ---------------- 포트폴리오 ----------------
def _victim(pos, frames, d, mode, new_score):
    """갈아타기: 팔 보유 종목 고르기 (지난주 종가까지의 정보만 사용, 4주 이상 보유한 종목만)"""
    best, best_sc = None, None
    for t, p in pos.items():
        if p.get("add") or (d - p["r"]["entry_date"]).days < 28:
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


def run_portfolio(sig, frames, cal, min_score, max_pos=None, rotate=None, rot_min=80, fill=None, max_small=None, caps=None, order=None, pause=None):
    """pause: (n, k, mode) 손절(R<=0)이 n번 연속이면 mode='stop': k주 동안 신규 매수 쉼 / 'half': k주 동안 위험을 절반으로"""
    """order: 같은 주·같은 점수일 때 이 열이 작은 신호부터 (예: beta)
       추매 행(add=True): 같은 종목을 들고 있을 때만 사고, 종목 수에 세지 않음 · 수량은 위험 절반, 비중 상한 절반
    fill: 남는 돈을 ETF로 채우기. dict(o=시가, c=종가, mkt=시장 단계, frac=채울 비율, half3=3단계면 절반, filt=4단계면 매도, band=리밸런싱 폭%)"""
    mp = max_pos or BT["max_pos"]
    etf_sh, inv_sum = 0.0, 0.0
    etf_tx = 0                                       # ETF 사고판 횟수 (리밸런싱·매수 자금 마련 매도)
    streak, pause_until, n_pause = 0, None, 0        # 연속 손절 수, 쉬는 기간 끝, 쉰 횟수
    cands = sig[(sig["score"] >= min_score)]
    if order:
        cands = cands.sort_values(["entry_date", "score", order], ascending=[True, False, True])
    else:
        cands = cands.sort_values(["entry_date", "score"], ascending=[True, False])
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
                etf_tx += 1
        # 1) 이번 주 시가 매수 (지난주까지의 자금으로), 자리가 꽉 찼으면 갈아타기 규칙 확인
        paused = pause is not None and pause_until is not None and d < pause_until
        if d in by_week and not (paused and pause[2] == "stop"):
            for _, r in by_week[d].iterrows():
                is_add = r.get("add") == True                # 추매가 아닌 행은 NaN일 수 있음 (bool(NaN)은 참)
                key = r["ticker"] + "+" if is_add else r["ticker"]
                if key in pos or (is_add and r["ticker"] not in pos):
                    continue
                nbase = sum(1 for p in pos.values() if not p.get("add"))
                if not is_add and max_small is not None and caps.get(r["ticker"]) == "소형" and sum(caps.get(x) == "소형" for x in pos) >= max_small:
                    continue
                if not is_add and nbase >= mp:
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
                    pa = pos.pop(v + "+", None)          # 추매분도 같이 정리
                    if pa:
                        cash += pa["shares"] * px * (1 - cost)
                    rr = p["r"].copy()
                    risk0 = rr["entry"] - rr["stop0"]
                    rr["exit"], rr["exit_date"], rr["why"] = px, d, "갈아타기"
                    rr["R"] = (px - rr["entry"]) / risk0 if risk0 > 0 else 0
                    rr["ret"] = (px / rr["entry"] - 1) * 100 - 2 * BT["cost_pct"]
                    trades.append(rr)
                risk_ps = r["entry"] - r["stop0"]
                if risk_ps <= 0:
                    continue
                k_ = (0.5 if is_add else 1.0) * (0.5 if paused and pause[2] == "half" else 1.0)
                shares = equity * BT["risk_pct"] * k_ / 100 / risk_ps
                liquid = cash + (etf_sh * eo if eo is not None else 0)
                val = min(shares * r["entry"], equity * wcap * k_ / 100, liquid / (1 + 2 * cost))
                if val < equity * 0.01:
                    continue
                shares = val / r["entry"]
                cash -= val * (1 + cost)
                if cash < 0 and eo is not None:      # 모자라는 돈은 ETF를 팔아서
                    sell = min(-cash / (1 - cost), etf_sh * eo)
                    etf_sh -= sell / eo
                    cash += sell * (1 - cost)
                    etf_tx += 1
                pos[key] = dict(shares=shares, exit_date=r["exit_date"], exit=r["exit"], r=r, add=is_add)
        # 2) 이번 주 청산
        for t in list(pos):
            p = pos[t]
            if p["exit_date"] is not None and p["exit_date"] <= d:
                cash += p["shares"] * p["exit"] * (1 - cost)
                trades.append(p["r"])
                del pos[t]
                if pause is not None and not p.get("add"):
                    streak = streak + 1 if p["r"]["R"] <= 0 else 0
                    if streak >= pause[0]:
                        pause_until, streak, n_pause = d + pd.Timedelta(weeks=pause[1]), 0, n_pause + 1
        # 3) 주말 평가
        mv = 0.0
        for t, p in pos.items():
            cl = frames[p["r"]["ticker"]]["close"]
            px = cl.asof(d)
            mv += p["shares"] * (px if pd.notna(px) else p["r"]["entry"])
        ev = 0.0
        if fill is not None and etf_sh:
            ec = fill["c"].asof(d)
            ev = etf_sh * (ec if pd.notna(ec) else (eo or 0))
        equity = cash + mv + ev
        curve.append((d, equity, len(pos), mv / equity * 100 if equity else 0, ev / equity * 100 if equity else 0))
    eq = pd.DataFrame(curve, columns=["date", "equity", "n", "stk", "etf"]).set_index("date")
    eq.attrs["etf_tx"] = etf_tx
    eq.attrs["n_pause"] = n_pause
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


def write_report(path, args, curves, cstats, tstats, variants, buckets, sigtab, yearly, sig, survivors_note, comp=None, best=None, exit_curves=None, seg=None,
                 improve=None, imp_curves=None, imp_base=None, qqq_entry=None, lev=None):
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
        P.append("<h2>장중 자동 매도 vs 금요일 종가 + 비상 손절 (+ 아래 신호 표에 실적 발표·갭 결과)</h2><div class=mut style='margin-bottom:6px'>모두 6종목 · 소형주 제외 · 남는 돈은 QQQ(시장 4단계면 매도) · 돌파 매수 스톱 -5% · 풀백은 30주선 근처 · 4단계까지 보유. 점수만 바꿨어요. "
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
    if qqq_entry:
        P.append("<h2>QQQ 진입 시점 (장기 보유용)</h2><div class=mut style='margin-bottom:6px'>2006년 이후 매주를 '살 돈이 생긴 주'로 보고, 방식별로 3년·5년 뒤 평가액을 "
                 "'바로 한 번에'와 비교했어요. 평균 = 바로 산 경우보다 몇 % 많거나 적었나, 이긴 비율 = 바로 산 경우보다 나았던 시작 주의 비율. "
                 "기다리는 동안 현금 이자는 0으로 계산했어요(기다리는 쪽에 조금 불리). 30주선 매수는 지난주 30주선 가격에 지정가로 산다고 봤어요.</div>"
                 "<div class=tw><table><tr><th class=l>방식</th><th>3년 뒤 평균</th><th>3년 뒤 이긴 비율</th><th>5년 뒤 평균</th><th>5년 뒤 이긴 비율</th>"
                 "<th>5년 뒤 최악</th><th>첫 매수까지 (중앙값, 주)</th><th>5년 안에 다 못 산 비율</th></tr>")
        for r in qqq_entry:
            P.append(f"<tr><td class=l><b>{e(r['name'])}</b></td><td>{sgn(r['rel156'])}</td><td>{f_(r['win156'], 0, True)}</td>"
                     f"<td>{sgn(r['rel260'])}</td><td>{f_(r['win260'], 0, True)}</td><td>{sgn(r['worst260'])}</td>"
                     f"<td>{f_(r['wait'], 0)}</td><td>{f_(r['miss5'], 0, True)}</td></tr>")
        P.append("</table></div>")
    if lev and lev[0]:
        P.append("<h2>레버리지 ETF · 섞기 · 코어-위성</h2><div class=mut style='margin-bottom:6px'>QLD·TQQQ는 2008년을 포함하려고 QQQ 일봉으로 재현했어요"
                 "(하루 수익률 ×2·×3 - 보수 0.95% - 차입 비용, 매일 리밸런싱). 아래 작은 표에서 실제 상품과 재현값을 비교할 수 있어요. 섞기·코어-위성은 4주마다 비중을 다시 맞추고 그 비용은 뺐어요. "
                 "'헬스케어'는 종목 목록에 세부 업종이 없어 GICS 헬스케어 전체(바이오·제약·의료기기·서비스)로 근사했어요.</div>"
                 f"<div class=tw><table><tr><th class=l>방식</th><th>연평균</th><th>최대 낙폭</th><th>연평균/최대낙폭</th><th>2008년</th><th>2022년</th><th>최악의 해</th>"
                 f"<th>~{IMPROVE_SPLIT.year - 1} 연평균</th><th>{IMPROVE_SPLIT.year}~ 연평균</th></tr>")
        for r in lev[0]:
            c_ = r["c"]
            dsc = f"<div class=mut style='font-size:12px;white-space:normal;min-width:200px'>{e(r['desc'])}</div>" if r["desc"] else ""
            P.append(f"<tr><td class=l><b>{e(r['name'])}</b>{dsc}</td>"
                     f"<td>{sgn(c_.get('cagr'))}</td><td>{sgn(c_.get('mdd'))}</td><td>{f_(c_.get('mar'), 2)}</td><td>{sgn(r['y08'])}</td><td>{sgn(r['y22'])}</td><td>{sgn(r['worst'])}</td>"
                     f"<td>{sgn(r['a'].get('cagr'))}</td><td>{sgn(r['b'].get('cagr'))}</td></tr>")
        P.append("</table></div>")
        if lev[1]:
            P.append(f"<div class=tw style='margin-top:8px'><table><tr><th class=l>실제 vs 재현 ({e(lev[1][0]['since'])}~)</th><th>실제 연평균</th><th>재현 연평균</th><th>실제 최대 낙폭</th><th>재현 최대 낙폭</th></tr>")
            for r in lev[1]:
                P.append(f"<tr><td class=l>{e(r['name'])}</td><td>{sgn(r['act'].get('cagr'))}</td><td>{sgn(r['sim'].get('cagr'))}</td><td>{sgn(r['act'].get('mdd'))}</td><td>{sgn(r['sim'].get('mdd'))}</td></tr>")
            P.append("</table></div>")
    if improve:
        P.append(f"<h2>수익률 개선 후보</h2><div class=mut style='margin-bottom:6px'><b>{e(imp_base or best or '')}</b> 규칙(앱 주문 요령 · 6종목 · QQQ 채우기)에 하나씩 바꿔 본 결과. "
                 f"필터는 이 20년 데이터의 신호 표를 보고 고른 것이라, 앞 기간(~{IMPROVE_SPLIT.year - 1})과 뒤 기간({IMPROVE_SPLIT.year}~) 모두에서 '지금'보다 나아야 믿을 만해요. "
                 "반반 조합은 매주 50:50으로 맞춘다고 가정했고 그 리밸런싱 비용은 빠져 있어요.</div>"
                 "<div class=tw><table><tr><th class=l>변형</th><th>연평균</th><th>최대 낙폭</th><th>연평균/최대낙폭</th><th>거래</th><th>승률</th><th>평균 R</th>"
                 f"<th>~{IMPROVE_SPLIT.year - 1} 연평균</th><th>~{IMPROVE_SPLIT.year - 1} 낙폭</th><th>{IMPROVE_SPLIT.year}~ 연평균</th><th>{IMPROVE_SPLIT.year}~ 낙폭</th></tr>")
        for r in improve:
            c_, t_, a_, b_ = r["c"], r["t"], r["a"], r["b"]
            P.append(f"<tr><td class=l><b>{e(r['name'])}</b><div class=mut style='font-size:12px;white-space:normal;min-width:220px'>{e(r['desc'])}</div></td>"
                     f"<td>{sgn(c_.get('cagr'))}</td><td>{sgn(c_.get('mdd'))}</td><td>{f_(c_.get('mar'), 2)}</td>"
                     f"<td>{t_.get('n', '–')}</td><td>{f_(t_.get('win'), 0, True)}</td><td>{f_(t_.get('avgR'), 2)}</td>"
                     f"<td>{sgn(a_.get('cagr'))}</td><td>{sgn(a_.get('mdd'))}</td><td>{sgn(b_.get('cagr'))}</td><td>{sgn(b_.get('mdd'))}</td></tr>")
        P.append("</table></div>")
        if imp_curves:
            P.append("<div class=card style='margin-top:8px'>" + line_svg(imp_curves) + "</div>")
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


# ---------------- 수익률 개선 후보 ----------------
IMPROVE_SPLIT = pd.Timestamp("2016-01-01")   # 앞·뒤 기간을 나눠 필터가 두 기간 모두에서 통하는지 확인
IMPROVE_BASE = "금요일 종가 + 비상 손절 -3%"   # 앱 주문 요령과 같은 규칙. ★는 실행마다 데이터 차이로 바뀔 수 있어 기준은 고정


TECH_COLS = ("minervini", "rs_rank", "mom_rank", "rsi14", "macd_up", "adx14", "golden", "squeeze")


def tech_indicators(frames, cal):
    """유명한 기술적 지표를 주봉으로 (각 주 금요일까지의 값만)"""
    print("기술적 지표 계산 중 (미너비니·RS 순위·RSI·MACD·ADX·볼린저)...")
    for f in frames.values():
        if "rsi14" in f:
            continue
        c, h, l = f["close"], f["high"], f["low"]
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        ma40, ma10 = c.rolling(40).mean(), c.rolling(10).mean()      # 미너비니·볼린저·ATR은 screener.indicators에서 계산
        d = c.diff()
        g, ls = d.clip(lower=0).ewm(alpha=1 / 14).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14).mean()
        f["rsi14"] = 100 - 100 / (1 + g / ls)
        macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
        f["macd_up"] = (macd > macd.ewm(span=9).mean()).astype(float)
        up, dn = h.diff(), -l.diff()
        pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=f.index)
        mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=f.index)
        atr14 = tr.ewm(alpha=1 / 14).mean()
        pdi, mdi = 100 * pdm.ewm(alpha=1 / 14).mean() / atr14, 100 * mdm.ewm(alpha=1 / 14).mean() / atr14
        f["adx14"] = (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / 14).mean()
        f["golden"] = (ma10 > ma40).astype(float)
    # 전체 종목 대비 순위 (그 주에 데이터가 있는 종목끼리)
    cl = pd.concat({t: f["close"] for t, f in frames.items()}, axis=1)
    r12 = (cl / cl.shift(52) - 1).rank(axis=1, pct=True) * 100
    m121 = (cl.shift(4) / cl.shift(52) - 1).rank(axis=1, pct=True) * 100
    for t, f in frames.items():
        f["rs_rank"], f["mom_rank"] = r12[t].reindex(f.index), m121[t].reindex(f.index)


def tech_masks(tk):
    """(이름, 설명, 통과 여부 배열)"""
    return [
        ("미너비니 트렌드 템플릿", "30·40주선 위 · 30주선>40주선 · 40주선 상승 · 52주 저점 +30% 이상 · 52주 고점 -25% 이내", tk["minervini"] == 1),
        ("오닐 RS 순위 상위 20%", "12개월 수익률이 전체 종목 중 상위 20% (IBD RS 80+와 비슷)", tk["rs_rank"] >= 80),
        ("12-1 모멘텀 상위 30%", "최근 4주를 뺀 12개월 수익률이 상위 30%", tk["mom_rank"] >= 70),
        ("RSI(14주) 50~80", "주봉 RSI가 50 이상 80 이하일 때만 (약세·과열 제외)", (tk["rsi14"] >= 50) & (tk["rsi14"] <= 80)),
        ("MACD 시그널 위", "주봉 MACD(12·26)가 시그널(9) 위일 때만", tk["macd_up"] == 1),
        ("ADX(14주) 25 이상", "추세 강도 ADX가 25 이상일 때만", tk["adx14"] >= 25),
        ("골든크로스", "10주선이 40주선 위 (일봉 50·200일선과 비슷)", tk["golden"] == 1),
        ("볼린저 수축 뒤", "최근 8주 안에 볼린저 밴드 폭이 52주 하위 25%까지 좁아졌던 종목 (VCP와 비슷)", tk["squeeze"] == 1),
        ("미너비니 + RS 상위 20%", "트렌드 템플릿과 RS 순위 상위 20%를 모두 충족", (tk["minervini"] == 1) & (tk["rs_rank"] >= 80)),
    ]


def improve_study(frames, cal, dfs, args, best_run, CV, kw, mp, fill, msmall, capmap, near_earn, secmap=None):
    """지금 최적 규칙(best_run)에서 하나씩 바꿔 본 포트폴리오 결과. 반환: (표 행 목록, 곡선 dict)"""
    sig0, eq0, tr0 = best_run
    ms0 = args.min_score

    def weak_out(df):
        """신호 주(풀백은 매수 주)까지 알 수 있는 정보로 약한 셋업 빼기:
           풀백인데 최근 13주 안 30주선 -3% 이탈 · 30주선이 52주 최저보다 +60% 이상(후반) · 헤드앤숄더 천장 뒤 · 2주 안 실적 발표를 앞둔 풀백"""
        if df.empty:
            return df
        g = lambda col: np.array([frames[t].at[d, col] for t, d in zip(df["ticker"], df["sig_date"])], float)
        mr, rise, hst = g("min_rel13"), g("ma_rise52"), g("hs_top")
        ea = np.array([near_earn(t, d) for t, d in zip(df["ticker"], df["entry_date"])], float)
        pb = (df["signal"] == "풀백").values
        bad = (pb & (mr < 0.97)) | (rise >= S.CFG["late_rise"]) | (hst > 0) | (pb & (ea == 1))
        return df[~bad]

    def run(sg, ms=None, risk=None, mp_=None, order=None):
        old = BT["risk_pct"]
        if risk:
            BT["risk_pct"] = risk
        try:
            return run_portfolio(sg, frames, cal, ms or ms0, max_pos=mp_ or mp, fill=fill, max_small=msmall, caps=capmap, order=order)
        finally:
            BT["risk_pct"] = old

    # 베타: 신호 주까지 52주 주간 수익률로 S&P500 대비
    bench_r = dfs["^GSPC"]["Close"].pct_change()
    for f in frames.values():
        if "beta" not in f:
            r_ = f["close"].pct_change()
            b_ = bench_r.reindex(f.index)
            f["beta"] = r_.rolling(52, min_periods=40).cov(b_) / b_.rolling(52, min_periods=40).var()

    def tag(df, col, date_col="sig_date"):
        return np.array([frames[t].at[d, col] for t, d in zip(df["ticker"], df[date_col])], float) if len(df) else np.array([])

    def add_ons(df):
        """수익 +5% 중인 보유 종목이 10주선에 닿으면 추매 (RS>0·10주선 상승 중). 손절은 10주선 -5%, 원래 포지션이 먼저 끝나면 같이 정리"""
        out = []
        for _, r in df.iterrows():
            f = frames[r["ticker"]]
            j0 = f.index.get_loc(r["entry_date"])
            ex = f.index.get_loc(r["exit_date"]) if pd.notna(r["exit_date"]) else len(f) - 1
            o, lo, c = f["open"].values, f["low"].values, f["close"].values
            ma10 = f["close"].rolling(10).mean().values
            rs = f["rs"].values
            for k in range(j0 + 2, ex):
                ln = ma10[k - 1]
                if math.isnan(ln) or c[k - 1] <= r["entry"] * 1.05 or c[k - 1] <= ln * 1.01:
                    continue
                if not (rs[k - 1] > 0 and ma10[k - 1] > ma10[k - 2]):
                    continue
                if lo[k] <= ln * 1.01:
                    px = min(o[k], ln * 1.01)
                    st = ln * 0.95
                    tr = _hold_from(f, k, px, st, True, None, None, kw.get("stop_mode", "intra"), False, kw.get("emerg"))
                    xd, xp, why = tr[1], tr[3], tr[6]
                    if pd.notna(r["exit_date"]) and (xd is None or xd > r["exit_date"]):
                        xd, xp, why = r["exit_date"], r["exit"], r["why"]
                    out.append(dict(r, sig_date=f.index[k - 1], entry_date=f.index[k], exit_date=xd, entry=px, exit=xp, stop0=st,
                                    R=(xp - px) / (px - st), ret=(xp / px - 1) * 100 - 2 * BT["cost_pct"], why=why, add=True))
                    break
        return pd.concat([df, pd.DataFrame(out)], ignore_index=True) if out else df

    tech_indicators(frames, cal)
    print("수익률 개선 후보 돌리는 중...")
    sig0 = sig0.copy()
    sig0["beta"] = tag(sig0, "beta")
    sig0["mkt_s"] = tag(sig0, "mkt")
    v2 = CV(vol_need=2.0, **kw)
    v15 = CV(vol_need=1.5, **kw)
    b26 = CV(min_base=26, **kw)
    g2 = CV(max_gap=2, **kw)
    allf = weak_out(CV(vol_need=1.5, min_base=26, max_gap=2, **kw))
    q_eq = etf_timing(dfs, ["QQQ"], cal, 1, (1, 2, 3), (4,), bench=dfs["^GSPC"]["Close"])[0] if "QQQ" in dfs else None

    rows, eqs = [], {}
    def add(name, desc, res):
        e_, t_ = res
        s = e_["equity"] if isinstance(e_, pd.DataFrame) else e_
        rows.append(dict(name=name, desc=desc, c=stats_curve(s), t=stats_trades(t_) if t_ is not None else {},
                         a=stats_curve(s[s.index < IMPROVE_SPLIT]), b=stats_curve(s[s.index >= IMPROVE_SPLIT])))
        eqs[name] = s
        print(f"  {name}: 연평균 {f_(rows[-1]['c'].get('cagr'))}% · 최대낙폭 {f_(rows[-1]['c'].get('mdd'))}% · "
              f"앞 {f_(rows[-1]['a'].get('cagr'))}% · 뒤 {f_(rows[-1]['b'].get('cagr'))}%")

    def blend(s1, s2, w=0.5):          # 매주 w : 1-w로 맞춘 조합
        r = w * s1.pct_change() + (1 - w) * s2.reindex(s1.index, method="ffill").pct_change()
        return (1 + r.fillna(0)).cumprod() * 100

    add("지금", f"{ms0}점 이상 · 이 규칙 그대로 (볼린저 수축 가점 +{S.CFG['squeeze_pts']} 포함)", (eq0, tr0))
    for ms in (70, 75, 80, 85):
        if ms != ms0:
            add(f"점수 {ms}점 이상", f"매수 점수 기준만 {ms}점으로", run(sig0, ms=ms))
    add("돌파 거래량 2배 확인", "돌파로 산 주의 거래량이 평소 2배 미만이면 다음 주 시가에 매도", run(v2))
    add("돌파 거래량 1.5배 확인", "같은 규칙, 기준 1.5배", run(v15))
    add("기저 26주 미만 돌파 제외", "6개월 이상 다진 기저를 돌파할 때만 매수", run(b26))
    add("갭 2%+ 돌파 건너뜀", "앱 요령대로 스톱 지정가를 매수가 +2%로: 시가가 그보다 위에서 시작하면 사지 않음", run(g2))
    add("약한 셋업 제외", "30주선 이탈했던 풀백 · 52주 +60% 후반 · 헤드앤숄더 천장 뒤 · 실적 2주 앞 풀백 제외", run(weak_out(sig0)))
    add("필터 모두", "거래량 1.5배 확인 + 기저 26주 + 갭 2% + 약한 셋업 제외", run(allf))
    add("필터 모두 + 위험 1.5%", "위 + 1회 손절 시 계좌 1.5%", run(allf, risk=1.5))
    add("필터 모두 + 8종목", "위 + 동시 최대 8종목 (한 종목 최대 12.5%)", run(allf, mp_=8))
    # 베타
    bt = sig0["beta"]
    add("베타 1.6 이상 제외", "신호 주 52주 베타(S&P500 대비)가 1.6 이상인 종목은 안 삼", run(sig0[~(bt >= 1.6)]))
    add("베타 0.8 미만 제외", "베타가 0.8 미만인 종목은 안 삼", run(sig0[~(bt < 0.8)]))
    add("같은 점수면 저베타 먼저", "같은 주·같은 점수 신호가 겹치면 베타가 낮은 종목부터", run(sig0.assign(beta=bt.fillna(9)), order="beta"))
    # 갈아타기 · 추매 · 매도 · 시장
    def run_rot(mode):
        return run_portfolio(sig0, frames, cal, ms0, max_pos=mp, rotate=mode, rot_min=ms0 + 5, fill=fill, max_small=msmall, caps=capmap)
    add("약한 종목 갈아타기", f"자리가 꽉 찼을 때 {ms0 + 5}점+ 새 신호가 오면, 4주 넘게 든 종목 중 약해진 것(점수 50 미만·3단계·RS 하락·업종 3·4단계)을 팔고 교체", run_rot("weak"))
    add("점수 차 갈아타기", f"{ms0 + 5}점+ 새 신호보다 25점 이상 낮은 보유 종목과 교체", run_rot("gap"))
    add("10주선 추매", "+5% 수익 중 10주선에 닿으면 위험 절반만큼 추매 (손절 10주선 -5%)", run(add_ons(sig0)))
    add("8주 뒤 본전 손절", "매수 8주 뒤 종가가 수익이면 손절가를 매수가로", run(CV(be=8, **kw)))
    add("30주선 종가 -3% 이탈 매도", "4단계를 기다리지 않고, 금요일 종가가 30주선 -3% 아래면 그 종가에 매도", run(CV(ma_exit=3, **kw)))
    # 유명한 기술적 기법 결합 (신호 주 금요일까지의 값으로 거르기)
    tk = {c: tag(sig0, c) for c in TECH_COLS}
    for lab, desc, m in tech_masks(tk):
        add("[결합] " + lab, desc, run(sig0[m]))
    add("[결합] 샹들리에 청산", "4단계를 기다리지 않고, 매수 후 최고 종가 - ATR(10주)×3 아래로 금요일 마감하면 매도", run(CV(chand=3, **kw)))
    # 볼린저 수축 가점 크기 (지금 = screener CFG 값)
    sq = tag(sig0, "sqz_on")
    for alt in (0, 3, 5, 8):
        if alt != S.CFG["squeeze_pts"]:
            add("볼린저 가점 없음" if alt == 0 else f"볼린저 가점 +{alt}", f"볼린저 수축 뒤 신호 가점을 {S.CFG['squeeze_pts']}점 대신 {alt}점으로 (점수만 다시 매긴 근사치)",
                run(sig0.assign(score=np.clip(sig0["score"] + (sq == 1) * (alt - S.CFG["squeeze_pts"]), 0, 100))))
    add("시장 3단계면 신규 매수 보류", "나스닥·S&P 중 나쁜 쪽이 3단계인 주의 신호는 안 삼 (지금은 4단계만 보류)", run(sig0[sig0["mkt_s"] != 3]))
    if q_eq is not None:
        add("QQQ · 4단계 때만 현금", "비교용: QQQ를 들고 시장 4단계 때만 현금", (q_eq, None))
        add("반반: 지금 + QQQ", "자금 50%는 지금 전략, 50%는 QQQ(4단계면 현금)", (blend(eqs["지금"], q_eq), None))
        add("반반: 필터 모두 + QQQ", "자금 50%는 필터 모두, 50%는 QQQ(4단계면 현금)", (blend(eqs["필터 모두"], q_eq), None))
    keep = ["지금", "필터 모두", "반반: 필터 모두 + QQQ", "QQQ · 4단계 때만 현금"]
    curves = {k: eqs[k] / eqs[k].iloc[0] * 100 for k in keep if k in eqs}
    if "QQQ" in dfs:
        c = dfs["QQQ"]["Close"].reindex(cal, method="ffill")
        curves["QQQ"] = c / c.iloc[0] * 100
    # 코어-위성: 위성 후보 (지금 규칙 전체 / 헬스케어만 / 헬스케어 소형 포함 / 바이오 ETF 2단계만)
    secmap = secmap or {}
    sats = {"지금 전략": eq0["equity"]}
    hc = sig0[sig0["ticker"].map(secmap) == "Health Care"]
    if len(hc):
        sats["헬스케어 대·중형"] = run(hc)[0]["equity"]
        hs = CV(**dict(kw, small={}))
        hs = hs[hs["ticker"].map(secmap) == "Health Care"]
        if len(hs):
            sats["헬스케어 소형 포함"] = run_portfolio(hs, frames, cal, ms0, max_pos=mp, fill=fill, caps=capmap)[0]["equity"]
    if "XBI" in dfs:
        xi = dfs["XBI"].index
        print(f"  XBI 주봉 {len(xi)}개 · 기준 달력과 같은 날짜 {len(xi.intersection(cal))}개 · 첫 날짜 {xi[0]}")
        xe, xn, xp = etf_timing(dfs, ["XBI"], cal, 1, (2,), (1, 3, 4), bench=dfs["^GSPC"]["Close"])
        print(f"  XBI 2단계만: 매매 {xn}번 · 보유 기간 {xp:.0f}%")
        sats["바이오 ETF(XBI) 2단계만"] = xe
    lev = lev_study(dfs, cal, args.start, sats)
    sc_ = sig0[sig0["score"] >= 65]
    beta_rows = [(f"[베타] 65점+ 신호 · 베타 {lab}", stats_trades(sc_[(sc_["beta"] >= lo) & (sc_["beta"] < hi)]))
                 for lab, lo, hi in (("0.8 미만", -9, 0.8), ("0.8~1.2", 0.8, 1.2), ("1.2~1.6", 1.2, 1.6), ("1.6 이상", 1.6, 99))]
    sc_t = {c: tag(sc_, c) for c in TECH_COLS}
    for lab, _, m in tech_masks(sc_t):
        beta_rows.append((f"[결합] 65점+ 신호 · {lab} 충족", stats_trades(sc_[m])))
        beta_rows.append((f"[결합] 65점+ 신호 · {lab} 미충족", stats_trades(sc_[~m])))
    return rows, curves, beta_rows, lev


# ---------------- 레버리지 ETF · 섞기 · 코어-위성 ----------------
def daily_close(tickers, start):
    """야후 일봉 종가 (레버리지 재현용). 실패하면 빈 dict"""
    try:
        import yfinance as yf
        d = yf.download(list(tickers), start=start, interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=True)
        out = {}
        for t in tickers:
            try:
                out[t] = d[t]["Close"].dropna()
            except Exception:
                pass
        return out
    except Exception as ex:
        print("  일봉을 받지 못했어요:", ex)
        return {}


def weekly_of(daily, cal):
    """일봉 → 주봉 달력(cal: 그 주 첫날 표시)의 그 주 마지막 종가"""
    idx = pd.DatetimeIndex(cal) + pd.Timedelta(days=6)
    return pd.Series(daily.reindex(idx, method="ffill").values, index=cal)


def sim_lev(qd, irx, L, fee=0.95):
    """QQQ 일별 수익률 × L - 보수 - (L-1)×단기금리 로 레버리지 ETF 재현 (매일 리밸런싱)"""
    r = qd.pct_change()
    rf = (irx.reindex(r.index).ffill().fillna(0) / 100 / 252) if irx is not None else 0.0
    rl = L * r - fee / 100 / 252 - (L - 1) * rf
    return (1 + rl.fillna(0)).cumprod()


def mix(series, ws, every=4):
    """여러 곡선을 ws 비중으로 섞고 every주마다 비중을 다시 맞춤"""
    R = pd.concat([x.pct_change() for x in series], axis=1).fillna(0).values
    w = np.array(ws, float)
    v, tot = w.copy(), [1.0]
    for k in range(1, len(R)):
        v = v * (1 + R[k])
        if k % every == 0:
            v = v.sum() * w
        tot.append(v.sum())
    return pd.Series(tot, index=series[0].index) * 100


def timed(sw, stage):
    """지난주 QQQ 단계가 4면 그 주는 현금 (수수료 무시)"""
    r = sw.pct_change().fillna(0).values
    st = stage.shift(1).values
    r = np.where(st == 4, 0.0, r)
    return pd.Series(np.cumprod(1 + r), index=sw.index) * 100


def curve_row(name, desc, s):
    s = s.dropna()
    yr = s.resample("YE").last().pct_change() * 100
    return dict(name=name, desc=desc, c=stats_curve(s), a=stats_curve(s[s.index < IMPROVE_SPLIT]), b=stats_curve(s[s.index >= IMPROVE_SPLIT]),
                y08=yr.get(pd.Timestamp("2008-12-31"), np.nan), y22=yr.get(pd.Timestamp("2022-12-31"), np.nan), worst=yr.iloc[1:].min() if len(yr) > 1 else np.nan)


def lev_study(dfs, cal, start, sats):
    """QQQ·QLD·TQQQ 보유, 4단계면 현금, 섞기, 코어-위성. sats: {이름: 위성 곡선}"""
    if "QQQ" not in dfs:
        return [], []
    print("레버리지 ETF·코어-위성 비교 중 (QQQ 일봉으로 QLD·TQQQ 재현)...")
    dd = daily_close(["QQQ", "^IRX"], (pd.Timestamp(start) - pd.Timedelta(days=10)).date().isoformat())
    qw = dfs["QQQ"]["Close"].reindex(cal, method="ffill")
    if "QQQ" in dd:
        q1 = weekly_of(dd["QQQ"], cal)
        lv = {L: weekly_of(sim_lev(dd["QQQ"], dd.get("^IRX"), L), cal) for L in (2, 3)}
        how = "일봉 재현"
    else:                                                    # 일봉이 없으면 주봉으로 근사 (변동성 손실을 덜 반영 → 실제보다 좋게 나옴)
        q1 = qw
        r = qw.pct_change().fillna(0)
        lv = {L: (1 + L * r - 0.0095 / 52).cumprod() for L in (2, 3)}
        how = "주봉 근사(일봉 실패)"
    fq = S.indicators(dfs["QQQ"])
    qst = fq.apply(lambda r: (S.classify(r) or (None,))[0], axis=1).reindex(cal, method="ffill")
    QQQ, QLD, TQQQ = q1 / q1.iloc[0] * 100, lv[2] / lv[2].iloc[0] * 100, lv[3] / lv[3].iloc[0] * 100
    rows = [
        curve_row("QQQ 보유", "나스닥100 그냥 보유", QQQ),
        curve_row("QLD 보유 (2배)", f"{how}: QQQ 하루 수익률 ×2 - 보수 0.95% - 차입 비용", QLD),
        curve_row("TQQQ 보유 (3배)", f"{how}: QQQ 하루 수익률 ×3 - 보수 0.95% - 차입 비용 ×2", TQQQ),
        curve_row("QQQ · 4단계면 현금", "지난주 QQQ가 4단계면 그 주는 현금", timed(QQQ, qst)),
        curve_row("QLD · 4단계면 현금", "", timed(QLD, qst)),
        curve_row("TQQQ · 4단계면 현금", "", timed(TQQQ, qst)),
        curve_row("QQQ 50 + QLD 50", "4주마다 비중 맞춤 (전체 약 1.5배)", mix([QQQ, QLD], [0.5, 0.5])),
        curve_row("QQQ 80 + TQQQ 20", "4주마다 비중 맞춤 (전체 약 1.4배)", mix([QQQ, TQQQ], [0.8, 0.2])),
        curve_row("QQQ 70 + TQQQ 30", "4주마다 비중 맞춤 (전체 약 1.6배)", mix([QQQ, TQQQ], [0.7, 0.3])),
    ]
    core = {"QQQ": QQQ, "QLD": QLD}
    if "SPY" in dfs:
        sw = dfs["SPY"]["Close"].reindex(cal, method="ffill").bfill()
        SPY = sw / sw.iloc[0] * 100
        fs = S.indicators(dfs["SPY"])
        sst = fs.apply(lambda r: (S.classify(r) or (None,))[0], axis=1).reindex(cal, method="ffill")
        QS = mix([QQQ, SPY], [0.5, 0.5])
        rows += [curve_row("SPY 보유", "S&P500 그냥 보유", SPY), curve_row("SPY · 4단계면 현금", "지난주 SPY가 4단계면 그 주는 현금", timed(SPY, sst)),
                 curve_row("QQQ 50 + SPY 50", "4주마다 비중 맞춤", QS)]
        core.update({"SPY": SPY, "QQQ·SPY 반반": QS})
    sats = {nm: (sat.reindex(cal, method="ffill").bfill()) for nm, sat in sats.items()}
    for nm, sat in sats.items():
        rows.append(curve_row(f"위성만: {nm}", "위성 전략 100%", sat / sat.iloc[0] * 100))
    for cn, cs in core.items():
        for nm, sat in sats.items():
            for w in (((0.8, 0.2), (0.7, 0.3)) if cn == "QQQ" else ((0.8, 0.2), (0.7, 0.3), (0.5, 0.5)) if cn in ("SPY", "QQQ·SPY 반반") else ((0.8, 0.2),)):
                rows.append(curve_row(f"코어 {cn} {int(w[0] * 100)} + {nm} {int(w[1] * 100)}", "4주마다 비중 맞춤", mix([cs, sat], list(w))))
    real = []
    if "TQQQ" in dfs and "QLD" in dfs:
        t0 = max(dfs["TQQQ"].index[0], pd.Timestamp(start))
        win = [d for d in cal if d >= t0]
        for nm, act, sim in (("QLD", dfs["QLD"]["Close"], QLD), ("TQQQ", dfs["TQQQ"]["Close"], TQQQ)):
            a = act.reindex(win, method="ffill"); b = sim.reindex(win)
            real.append(dict(name=nm, act=stats_curve(a / a.iloc[0] * 100), sim=stats_curve(b / b.iloc[0] * 100), since=str(t0.date())))
    for r in rows:
        print(f"  [레버리지·코어] {r['name']}: 연평균 {f_(r['c'].get('cagr'))}% · 최대낙폭 {f_(r['c'].get('mdd'))}% · 2008 {f_(r['y08'])}%")
    return rows, real


# ---------------- 횡보장: 박스 아래에서 사고 위에서 팔기 ----------------
def market_side(dfs, cal, t="^GSPC"):
    """주별 시장 횡보 판정 (그 주까지의 데이터만): A = 30주선 10주 변화 ±평평 기준 안 또는 15주 동안 30주선을 3번 이상 넘나듦 / B = 주봉 ADX(14) 20 미만"""
    f = S.indicators(dfs[t])
    flat = S.CFG["flat_pct"]
    a = (f["slope"].abs() <= flat) | (f["crosses"] >= S.CFG["whipsaw"])
    h, l, c = f["high"], f["low"], f["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=f.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=f.index)
    atr = tr.ewm(alpha=1 / 14).mean()
    pdi, mdi = 100 * pdm.ewm(alpha=1 / 14).mean() / atr, 100 * mdm.ewm(alpha=1 / 14).mean() / atr
    adx = (100 * (pdi - mdi).abs() / (pdi + mdi)).ewm(alpha=1 / 14).mean()
    b = adx < 20
    return a.reindex(cal, method="ffill").fillna(False).astype(bool), b.reindex(cal, method="ffill").fillna(False).astype(bool)


def range_signals(frames, start, box=26, lo_zone=0.25, tgt=0.85, min_w=12, max_w=50, max_hold=26):
    """박스권 매매: 30주선이 평평한 종목(1·3단계), 최근 box주 고가·저가로 박스(폭 min_w~max_w%).
       금요일 종가가 박스 아래 lo_zone 구간 안 · 박스 아래 끝은 지킴 · 양봉(반등) → 다음 주 시가 매수.
       손절 = 박스 저점 -3% (장중), 목표 = 박스 아래에서 tgt 지점 (장중 지정가), max_hold주 지나면 종가 매도."""
    rows = []
    flat = S.CFG["flat_pct"]
    for t, f in frames.items():
        o, h, lo, c, sl, dv = (f[k].values for k in ("open", "high", "low", "close", "slope", "dollar_vol"))
        hh = f["high"].rolling(box).max().shift(1).values
        ll = f["low"].rolling(box).min().shift(1).values
        n, i, idx = len(f), box + 1, f.index
        while i < n - 1:
            if idx[i] < start or math.isnan(hh[i]) or math.isnan(sl[i]) or abs(sl[i]) > flat or c[i] < S.CFG["min_price"] \
                    or (dv[i] if not math.isnan(dv[i]) else 0) < S.CFG["min_dollar_vol"]:
                i += 1; continue
            H, L = hh[i], ll[i]
            w = (H / L - 1) * 100
            if not (min_w <= w <= max_w) or not (L <= lo[i]) or not (c[i] <= L + lo_zone * (H - L)) or not (c[i] > o[i]):
                i += 1; continue
            e, s0, T = o[i + 1], L * 0.97, L + tgt * (H - L)
            if not (e > s0 > 0) or e >= T:
                i += 1; continue
            j, px, why = i + 1, None, None
            while j < n:
                if lo[j] <= s0:
                    px, why = min(s0, o[j]), "박스 이탈 손절"; break
                if h[j] >= T:
                    px, why = max(T, o[j]), "박스 위 목표가"; break
                if j - i >= max_hold:
                    px, why = c[j], "기간 만료"; break
                j += 1
            ex = idx[j] if j < n else None
            if px is None:
                px, why = c[-1], "보유 중"
            rows.append(dict(ticker=t, sig_date=idx[i], entry_date=idx[i + 1], exit_date=ex, entry=e, exit=px, stop0=s0,
                             R=(px - e) / (e - s0), ret=(px / e - 1) * 100 - 2 * BT["cost_pct"], weeks=(j - i - 1) if j < n else n - i - 2,
                             why=why, score=100.0 - (c[i] - L) / (H - L) * 20, signal="박스", stage_label="", dup=False, width=w))
            i = j + 1 if j < n else n
    return pd.DataFrame(rows)


def range_study(frames, cal, dfs, start, base_run, ms0, mp, fill, msmall, capmap):
    """횡보장 전환: 시장 횡보(A·B)일 때 와인스테인 신규 매수 대신 박스권 매매. 반환: (곡선 표 행, 거래 통계 행)"""
    sideA, sideB = market_side(dfs, cal)
    sig0, eq0, _ = base_run
    rs = range_signals(frames, start)
    print(f"  박스권 신호 {len(rs)}개 · 시장 횡보 비율 A {sideA[sideA.index >= start].mean() * 100:.0f}% · B {sideB[sideB.index >= start].mean() * 100:.0f}%")
    def on(sd, df):
        return sd.reindex(df["sig_date"], method="ffill").fillna(False).values.astype(bool)
    def run(sg):
        return run_portfolio(sg, frames, cal, ms0, max_pos=mp, fill=fill, max_small=msmall, caps=capmap)
    rows, trows = [], []
    rows.append(curve_row("지금 전략 (항상 와인스테인)", "비교 기준", eq0["equity"]))
    if len(rs):
        e_r, t_r = run(rs)
        rows.append(curve_row("박스권 매매만 (항상)", "박스 아래 25% 구간 반등 매수 · 위 85% 목표 · 박스 -3% 손절", e_r["equity"]))
        for nm, sd in (("A: 30주선 평평·넘나듦", sideA), ("B: ADX 20 미만", sideB)):
            w = sig0[~on(sd, sig0)]
            r_ = rs[on(sd, rs)]
            rows.append(curve_row(f"횡보({nm})면 신규 매수 쉼", "횡보 판정된 주에는 와인스테인 신규 매수 안 함 (보유분은 그대로)", run(w)[0]["equity"]))
            mix_ = pd.concat([w, r_], ignore_index=True)
            mix_["score"] = mix_["score"].clip(lower=ms0)
            rows.append(curve_row(f"횡보({nm})면 박스권 매매로 전환", "추세장 = 와인스테인 · 횡보장 = 박스권 매매 (같은 계좌·같은 종목 수)", run(mix_)[0]["equity"]))
        cl = rs.copy()
        cl["sm"] = cl["ticker"].map(capmap) == "소형"
        a_, b_ = on(sideA, cl), on(sideB, cl)
        for lab, m in [("박스권 매매 · 전체", np.ones(len(cl), bool)), ("· 시장 횡보(A)일 때", a_), ("· 시장 추세(A 아님)일 때", ~a_),
                       ("· 시장 횡보(B: ADX<20)일 때", b_), ("· 시장 추세(B 아님)일 때", ~b_),
                       ("· 대·중형", ~cl["sm"].values), ("· 소형", cl["sm"].values),
                       ("· 박스 폭 12~25%", (cl["width"] < 25).values), ("· 박스 폭 25~50%", (cl["width"] >= 25).values),
                       ("· ~2015", (cl["sig_date"] < IMPROVE_SPLIT).values), ("· 2016~", (cl["sig_date"] >= IMPROVE_SPLIT).values)]:
            st_ = stats_trades(cl[m])
            trows.append(("[박스권] " + lab, st_))
            print(f"  [박스권] {lab}: {st_.get('n', 0)}개 · 승률 {f_(st_.get('win'))}% · 평균 {f_(st_.get('ret'))}% · {f_(st_.get('avgR'), 2)}R")
    for r in rows:
        print(f"  [횡보 전환] {r['name']}: 연평균 {f_(r['c'].get('cagr'))}% · 최대낙폭 {f_(r['c'].get('mdd'))}% · 앞 {f_(r['a'].get('cagr'))}% · 뒤 {f_(r['b'].get('cagr'))}%")
    return rows, trows


# ---------------- QQQ 진입 시점 (장기 보유용) ----------------
def qqq_entry_study(dfs, start, etf="QQQ"):
    """start 이후 매주를 '돈이 생긴 주'로 보고, 진입 방식별로 3년·5년 뒤 평가액을 '바로 한 번에'와 비교.
       각 주의 판단은 그 주까지의 데이터만 사용. 기다리는 동안 현금 이자는 0 (기다리는 쪽에 조금 불리)"""
    if etf not in dfs:
        return []
    f = S.indicators(dfs[etf])
    st = f.apply(lambda r: (S.classify(r) or (None,))[0], axis=1).values
    o, l, c, ma = f["open"].values, f["low"].values, f["close"].values, f["ma30"].values
    n = len(f)
    i0 = int(f.index.searchsorted(pd.Timestamp(start)))

    def buys(i, rule):
        """돈이 생긴 주 i (금요일까지 봄) → [(매수 주, 가격, 비중)]"""
        if i + 1 >= n:
            return []
        if rule == "now":
            return [(i + 1, o[i + 1], 1.0)]
        if rule == "dca":
            return [(i + 1 + 4 * k, o[i + 1 + 4 * k], 1 / 6) for k in range(6) if i + 1 + 4 * k < n]
        if rule in ("ma30", "half"):
            got = []
            for j in range(i + 1, n):
                lim = ma[j - 1]                       # 지난주까지의 30주선에 지정가
                if not np.isnan(lim) and l[j] <= lim:
                    got = [(j, min(o[j], lim), 1.0)]
                    break
            if rule == "half":
                return [(i + 1, o[i + 1], 0.5)] + [(j, p, 0.5) for j, p, _ in got]
            return got
        if rule == "st4":
            for j in range(i, n - 1):
                if st[j] == 4:
                    return [(j + 1, o[j + 1], 1.0)]
            return []
        if rule == "st4end":
            seen = False
            for j in range(i, n - 1):
                if st[j] == 4:
                    seen = True
                elif seen and st[j] in (1, 2):
                    return [(j + 1, o[j + 1], 1.0)]
            return []
        return []

    rules = [("바로 한 번에", "now"), ("6개월 분할 (4주마다 1/6)", "dca"), ("30주선 닿을 때까지 기다림", "ma30"),
             ("절반 바로 + 절반 30주선 닿을 때", "half"), ("4단계가 오면 매수", "st4"), ("4단계 뒤 1·2단계로 돌아서면 매수", "st4end")]
    cache = {r: [buys(i, r) for i in range(i0, n)] for _, r in rules}

    def value(bl, h):
        """h주 종가 기준 평가액 (처음 돈 = 1). 아직 못 산 돈은 현금"""
        v, used = 0.0, 0.0
        for j, p, w in bl:
            if j <= h:
                v += w / p * c[h]
                used += w
        return v + (1 - used)

    rows = []
    for lab, r in rules:
        out = dict(name=lab)
        for H in (156, 260):
            rel, wins, cnt = [], 0, 0
            for k, i in enumerate(range(i0, n)):
                h = i + H
                if h >= n:
                    break
                vr, vn = value(cache[r][k], h), value(cache["now"][k], h)
                rel.append(vr / vn - 1)
                wins += vr > vn * 1.0001
                cnt += 1
            out[f"rel{H}"] = np.mean(rel) * 100 if rel else np.nan
            out[f"worst{H}"] = np.min(rel) * 100 if rel else np.nan
            out[f"win{H}"] = wins / cnt * 100 if cnt else np.nan
        waits = [bl[0][0] - (i0 + k) for k, bl in enumerate(cache[r]) if bl]
        late = [k for k, bl in enumerate(cache[r]) if (i0 + k + 260 < n) and (not bl or bl[-1][0] > i0 + k + 260)]
        tot5 = sum(1 for k in range(len(cache[r])) if i0 + k + 260 < n)
        out["wait"] = float(np.median(waits)) if waits else np.nan
        out["miss5"] = len(late) / tot5 * 100 if tot5 else np.nan
        rows.append(out)
        print(f"  [{etf} 진입] {lab}: 3년 뒤 {f_(out['rel156'])}% · 5년 뒤 {f_(out['rel260'])}% (바로 대비) · 이긴 비율 {f_(out['win260'], 0)}%")
    return rows


# ---------------- 메인 ----------------
def main(argv=None, fetch=None, universe=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2006-01-01")
    ap.add_argument("--min-score", type=int, default=80)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--web", action="store_true", help="앱 안에 넣을 리포트 (앱으로 돌아가는 링크 포함)")
    args = ap.parse_args(argv)
    start = pd.Timestamp(args.start)
    uni = universe or S.load_universe(args.quick)
    uni = {k: v for k, v in uni.items() if v.get("bench") == "us"}
    if not S.ADR_BUY:      # 앱과 같게: 해외 ADR(내 관심 종목 제외)은 매수 대상에서 뺌
        uni = {k: v for k, v in uni.items() if v.get("src") not in ("adr", "adr_mid") or v.get("mine")}
    years = (pd.Timestamp.today() - start).days / 365.25 + 3      # 지표 계산용 3년 더
    need = list(uni) + list(S.SECTOR_ETF.values()) + list(S.INDUSTRY_ETF) + list(S.MARKET) + ["SPY", "QQQ", "QLD", "TQQQ"]
    print(f"{len(need)}개 종목의 약 {years:.0f}년치 주봉을 받습니다 (처음엔 5~15분 걸릴 수 있어요)")
    dfs = (fetch or (lambda n: S.fetch_weekly(n, years=years)))(need)
    if "^GSPC" not in dfs:
        sys.exit("S&P500 지수 데이터를 받지 못했습니다. 인터넷 연결을 확인하고 다시 실행하세요.")
    frames, market = build_frames(dfs, uni)
    cal = dfs["^GSPC"].index[dfs["^GSPC"].index >= start]
    print("손절 방식별로 돌리는 중... (매수: 돌파 매수 스톱·-5% / 풀백, 4단계까지 보유)")
    EXIT.clear(); EXIT.update(EXITS["E 4단계까지 보유"])
    STOP.clear(); STOP.update(mode="entry", pct=S.CFG["breakout_stop_pct"])
    base_sig = collect_variant(frames, start, "base", "stop")
    if base_sig.empty:
        sys.exit("기간 안에 신호가 하나도 없습니다. --start 날짜나 점수 기준을 확인하세요.")
    def F(etf, frac=1.0, half3=False, filt=True):
        if etf not in dfs:
            return "skip"
        x = dfs[etf]
        return dict(o=x["Open"], c=x["Close"], mkt=market, frac=frac, half3=half3, filt=filt, band=25)   # 앱과 같게: 차이 25% 넘을 때만 QQQ 맞춤
    q = F("QQQ")
    capmap = {t: v.get("cap") for t, v in uni.items()}
    try:
        crash_study(frames, start, capmap, fetch_earnings(sorted(t for t in frames if capmap.get(t) != "소형")))
    except Exception as ex:
        print("  급락 후 분석 실패:", ex)
    try:
        for f in frames.values():
            pattern_flags(f)
        pattern_study(frames, start, capmap)
    except Exception as ex:
        print("  패턴 분석 실패:", ex)
    CV = lambda **kw: collect_variant(frames, start, "base", "stop", caps=capmap, **kw)
    ATRV = dict(atr=True)
    OUT = dict(out=True)
    TP = "score2TPt"
    small_t = [t for t in frames if capmap.get(t) == "소형"]
    rk_small = pd.concat({t: frames[t]["dollar_vol"] for t in small_t}, axis=1).rank(axis=1, ascending=False) if small_t else pd.DataFrame()
    base_kw = dict(small=OUT, score_col="score2TPt", b12=10, block_rebreak=13)
    SK = {"cur": {}, "close": dict(stop_mode="close"), **{f"e{x}": dict(stop_mode="close", emerg=x) for x in (3, 5, 8)}}
    sigs = {k: CV(**kw, **base_kw) for k, kw in SK.items()}
    sigs[""] = sigs["cur"]
    sig_inc = CV(score_col="score2TPt")          # 소형주 포함 (신호 분석용)
    bk = sigs["cur"]
    bk = bk[bk["kind"].isin(["B12", "B2"]) & (bk["score"] >= args.min_score)]
    es = entry_study(frames, bk)
    exs = ext_study(frames, start, capmap)
    # 실적 발표 날짜 (야후). 매수 후 2주 안에 실적 발표가 있었는지
    earn = fetch_earnings(sorted(set(sigs["cur"]["ticker"])))
    def near_earn(t, d, days=14):
        ds = earn.get(t)
        if ds is None or len(ds) == 0:
            return np.nan
        k = ds.searchsorted(d)
        return float(k < len(ds) and (ds[k] - d).days <= days)
    GRID = [  # (이름, 최대 보유 종목 수, 남는 돈 채우기, 설명, 신호, 소형주 최대 종목 수)
        ("장중 자동 매도 (지금)", 6, q, "손절가 닿으면 장중 자동 매도", "cur", None),
        ("금요일 종가 판단만", 6, q, "금요일 종가가 손절가 아래면 그 종가에 매도", "close", None),
        ("금요일 종가 + 비상 손절 -3%", 6, q, "금요일 종가 판단 + 손절가보다 3% 아래는 장중 자동 매도", "e3", None),
        ("금요일 종가 + 비상 손절 -5%", 6, q, "비상 손절 손절가 -5%", "e5", None),
        ("금요일 종가 + 비상 손절 -8%", 6, q, "비상 손절 손절가 -8%", "e8", None),
    ]
    comp, runs, combos = [], {}, {}
    for name, mp_, fl, why, sk, msmall in GRID:
        if fl == "skip":
            fl = None
        base_sig = sigs[sk]
        pbs = stats_trades(base_sig[(base_sig["signal"] == "풀백") & (base_sig["score"] >= args.min_score)])
        e_, t_ = run_portfolio(base_sig, frames, cal, args.min_score, max_pos=mp_, fill=fl, max_small=msmall, caps=capmap)
        cs, ts = stats_curve(e_["equity"]), stats_trades(t_)
        smt = stats_trades(t_[t_["ticker"].map(capmap) == "소형"]) if len(t_) else {}
        desc = (f"{why} · 한 종목 최대 {min(BT['max_weight'], 100 / mp_):.0f}% · 평균 보유 {e_['n'].mean():.1f}종목 · "
                f"평균 투자 비중: 종목 {e_['stk'].mean():.0f}% + QQQ {e_['etf'].mean():.0f}%"
                + f" · 실제 거래 중 소형주 {smt.get('n', 0)}건" + (f" (승률 {f_(smt.get('win'), 0)}% · 평균 {f_(smt.get('avgR'))}R)" if smt.get("n") else ""))
        comp.append(dict(name=name, desc=desc, c=cs, t=ts, hiR=stats_trades(base_sig[base_sig["score"] >= 75]).get("avgR"),
                         loR=stats_trades(base_sig[base_sig["score"] < 65]).get("avgR")))
        runs[name] = (base_sig, e_, t_)
        combos[name] = (2, "entry", S.CFG["breakout_stop_pct"], "E 4단계까지 보유", mp_, fl, msmall, sk)
        print(f"  {name}: 연평균 {f_(cs.get('cagr'))}% · 최대낙폭 {f_(cs.get('mdd'))}% · 소형주 거래 {smt.get('n', 0)} {f_(smt.get('avgR'))}R")
    if not runs:
        sys.exit("기간 안에 신호가 하나도 없습니다. --start 날짜나 점수 기준을 확인하세요.")
    best = max(comp, key=lambda r: (r["c"].get("mar") if pd.notna(r["c"].get("mar")) else -99))["name"]   # 연평균/최대낙폭이 가장 좋은 조합
    ver_b, smode_b, pct_b, ex_b, mp_b, fill_b, ms_b, sk_b = combos[best]
    EXIT.clear(); EXIT.update(EXITS[ex_b])
    STOP.clear(); STOP.update(mode=smode_b, pct=pct_b)
    for f in frames.values():
        f["score"], f["signal"] = f[f"score{ver_b}"], f[f"signal{ver_b}"]
    sig, eq, trades = runs[best]
    sig["cap"] = sig["ticker"].map(capmap)
    seg = []
    for cp in ("대형", "중형", "소형"):
        ss = sig[sig["cap"] == cp]
        if ss.empty:
            continue
        e3, t3 = run_portfolio(ss, frames, cal, args.min_score, max_pos=mp_b, fill=fill_b, max_small=ms_b, caps=capmap)
        cl3 = ss[~ss["dup"]]
        seg.append(dict(name=cp, n_t=int(ss["ticker"].nunique()), c=stats_curve(e3["equity"]), t=stats_trades(t3), s=stats_trades(cl3)))
        print(f"  {cp}: 연평균 {f_(seg[-1]['c'].get('cagr'))}% · 최대낙폭 {f_(seg[-1]['c'].get('mdd'))}%")
    ib = IMPROVE_BASE if IMPROVE_BASE in runs else best
    *_, mp_i, fill_i, ms_i, sk_i = combos[ib]
    qqq_entry = qqq_entry_study(dfs, start)
    improve, imp_curves, beta_rows, lev = improve_study(frames, cal, dfs, args, runs[ib], CV, dict(SK[sk_i], **base_kw), mp_i, fill_i, ms_i, capmap, near_earn,
                                                         secmap={t: v.get("sector") for t, v in uni.items()})
    _sec_bk = []
    try:
        lev[0].extend(sector_rs_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap, {t: v.get("sector") for t, v in uni.items()}, _sec_bk))
    except Exception as ex:
        print("  업종 RS 순위 비교 실패:", ex)
    try:
        lev[0].extend(climax_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  과열 매도 비교 실패:", ex)
    try:
        lev[0].extend(addon_profit_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  추매·익절 비교 실패:", ex)
    try:
        lev[0].extend(touch10_portfolio_study(frames, cal, start, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  10주선 포트폴리오 실패:", ex)
    try:
        lev[0].extend(breadth_study(frames, cal, dfs, market, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  시장 폭 비교 실패:", ex)
    try:
        lev[0].extend(sizing_study(frames, cal, runs[ib], args.min_score, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  비중 비교 실패:", ex)
    try:
        ex_fill = []
        sp = F("SPY")
        if sp != "skip" and fill_i:
            ex_fill.append(("S&P500(SPY)로 채움 · 차이 25%면 맞춤", dict(sp, band=25)))
            qc, scl = dfs["QQQ"]["Close"], dfs["SPY"]["Close"].reindex(dfs["QQQ"].index, method="ffill")
            blend = (1 + pd.concat([qc.pct_change(), scl.pct_change()], axis=1).mean(axis=1).fillna(0)).cumprod() * 100
            ex_fill.append(("QQQ 반 + SPY 반으로 채움 · 차이 25%면 맞춤", dict(fill_i, o=blend.shift(1).bfill(), c=blend, band=25)))
        lev[0].extend(fill_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap, ex_fill))
    except Exception as ex:
        print("  남는 돈 비교 실패:", ex)
    try:
        lev[0].extend(universe_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap,
                                     {t: v.get("src") for t, v in uni.items()}))
    except Exception as ex:
        print("  종목 범위 비교 실패:", ex)
    try:
        rrows, rtrows = range_study(frames, cal, dfs, start, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap)
        lev[0].extend(rrows)
    except Exception as ex:
        print("  횡보 전환 비교 실패:", ex); rtrows = []
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
        e2, t2 = run_portfolio(sig, frames, cal, ms, max_pos=mp_b, fill=fill_b, max_small=ms_b, caps=capmap)
        variants[f"{ms}점 이상"] = (stats_curve(e2["equity"]), stats_trades(t2))
    clean = sig[~sig["dup"]]
    bins = [(50, 65, "50–64 관찰"), (65, 75, "65–74 조건부"), (75, 80, "75–79"), (80, 90, "80–89 적극"), (90, 101, "90+")]
    buckets = [(lab, stats_trades(clean[(clean["score"] >= a) & (clean["score"] < b)])) for a, b, lab in bins]
    buckets.extend(_sec_bk)
    # 2단계 진행도·기저 길이별 (모든 신호, 지금 점수 50점+)
    allsig = sig_inc
    allsig = allsig[~allsig["dup"]].copy()
    _rt = CV(score_col="score2TPt", rt_stop=True)
    _rt = _rt[(_rt["kind"] == "RT") & (_rt["ticker"].map(capmap) != "소형")]
    buckets.append(("[종류] 돌파선 되돌림 · 돌파선 -3% 손절 (대·중형)", stats_trades(_rt)))
    def at(col):
        return [frames[t][col].asof(d) for t, d in zip(allsig["ticker"], allsig["sig_date"])]
    allsig["upw"], allsig["rise"], allsig["bl"] = at("ma_up_weeks"), at("ma_rise52"), at("base_len")
    allsig["up13"], allsig["mr13"], allsig["ihs"], allsig["hst"] = at("ma_up13"), at("min_rel13"), at("ihs"), at("hs_top")
    allsig["dd"] = at("dd52")
    allsig["x52"], allsig["dump"] = at("crosses52"), at("dump13")
    allsig["evr"] = [frames[t]["vr"].asof(d) for t, d in zip(allsig["ticker"], allsig["entry_date"])]   # 매수한 주 거래량 배수
    cs_ = sigs["cur"].copy()
    cs_["earn"] = [near_earn(t, d) for t, d in zip(cs_["ticker"], cs_["entry_date"])]
    have = cs_["earn"].notna()
    print(f"  실적 날짜 확인된 거래 {int(have.sum())}/{len(cs_)}")
    for lab, m in [(f"[실적] 매수 후 2주 안 실적 발표 있음 (날짜 확인 {int(have.sum())}건 중)", cs_["earn"] == 1), ("[실적] 2주 안 실적 발표 없음", cs_["earn"] == 0),
                   ("[실적] 돌파 · 실적 앞둠", (cs_["earn"] == 1) & (cs_["signal"] == "돌파")), ("[실적] 돌파 · 실적 없음", (cs_["earn"] == 0) & (cs_["signal"] == "돌파")),
                   ("[실적] 풀백 · 실적 앞둠", (cs_["earn"] == 1) & (cs_["signal"] == "풀백")), ("[실적] 풀백 · 실적 없음", (cs_["earn"] == 0) & (cs_["signal"] == "풀백"))]:
        buckets.append((lab, stats_trades(cs_[m])))
    bk2 = cs_[cs_["kind"].isin(["B12", "B2"])]
    for lab, m in [("[갭] 월요일 시가가 매수가 위로 갭 (시가 매수)", bk2["gap"] == "up"), ("[갭] · 그중 매수가보다 3%+ 위 갭", (bk2["gap"] == "up") & (bk2["gap_pct"] >= 3)),
                   ("[갭] 월요일 시가가 지난주 종가보다 2%+ 갭하락 뒤 돌파", bk2["gap"] == "down"), ("[갭] 갭 없이 주중 돌파", bk2["gap"] == "normal")]:
        buckets.append((lab, stats_trades(bk2[m])))
    allsig["sm"] = allsig["ticker"].map(capmap) == "소형"
    try:
        _FR.update(frames)
        _SECMAP.update({t: v.get("sector") for t, v in uni.items()})
        _GOLD.update({t: any(n == "금광" for n, _ in (v.get("themes") or [])) for t, v in uni.items()})
        big_winner_study(allsig, at, capmap, buckets)
    except Exception as ex:
        print("  큰 수익 종목 특징 분석 실패:", ex)
    try:
        kw_i = dict(SK[sk_i], **base_kw)
        lev[0].extend(supply_study(frames, cal, runs[ib], CV(**dict(kw_i, score_col="score2TPtS")), CV(**dict(kw_i, score_col="score2TPtS0")),
                                   args.min_score, mp_i, fill_i, ms_i, capmap, allsig, at, buckets))
    except Exception as ex:
        print("  전고점 매물 분석 실패:", ex)
    try:
        lev[0].extend(pause_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap))
    except Exception as ex:
        print("  연속 손절 비교 실패:", ex)
    try:
        lev[0].extend(pullback_quality_study(frames, cal, runs[ib], args.min_score, mp_i, fill_i, ms_i, capmap, buckets))
    except Exception as ex:
        print("  풀백 품질 비교 실패:", ex)
    try:
        vol2_study(allsig, frames, capmap, buckets)
    except Exception as ex:
        print("  2주 거래량 비교 실패:", ex)
    try:
        event_study(allsig, capmap, buckets)
    except Exception as ex:
        print("  이벤트 거래량 분석 실패:", ex)
    pb, bk = allsig["signal"] == "풀백", allsig["signal"] == "돌파"
    late = (allsig["upw"] >= S.CFG["late_weeks"]) | (allsig["rise"] >= S.CFG["late_rise"])
    def es_row(lab, pairs):
        if not pairs:
            buckets.append((lab, dict(n=0))); return
        a = np.array(pairs, float)
        buckets.append((lab, dict(n=len(a), win=(a[:, 0] > 0).mean() * 100, avgR=a[:, 1].mean(), medR=np.median(a[:, 1]),
                                  avgW=np.nan, avgL=np.nan, pf=np.nan, big=(a[:, 1] >= 3).mean() * 100, weeks=np.nan, ret=a[:, 0].mean())))
        print(f"  {lab}: {len(a)}개 · 평균 {a[:, 0].mean():.1f}% · {a[:, 1].mean():.2f}R")
    # ETF 와인스테인식 비교 (곡선 통계를 표에 넣음: 승률 칸=투자 기간 비율, 평균 R 칸=연평균, 수익률 칸=최대 낙폭)
    bench_s = dfs["^GSPC"]["Close"]
    secs = list(S.SECTOR_ETF.values())
    inds = secs + list(S.INDUSTRY_ETF)
    etf_rows = [("QQQ 그냥 보유", ["QQQ"], 1, (1, 2, 3, 4), ()), ("QQQ · 4단계 때만 현금", ["QQQ"], 1, (1, 2, 3), (4,)),
                ("QQQ · 2단계일 때만 보유", ["QQQ"], 1, (2,), (1, 3, 4)),
                ("SPY(VOO) 그냥 보유", ["SPY"], 1, (1, 2, 3, 4), ()), ("SPY(VOO) · 4단계 때만 현금", ["SPY"], 1, (1, 2, 3), (4,)),
                ("SPY(VOO) · 2단계일 때만 보유", ["SPY"], 1, (2,), (1, 3, 4)),
                ("섹터 ETF 11개 · 2단계 RS 상위 3개 · 4단계 매도", secs, 3, (2,), (4,)),
                ("섹터 ETF 11개 · 2단계 RS 상위 3개 · 3·4단계 매도", secs, 3, (2,), (3, 4)),
                ("섹터+산업 ETF · 2단계 RS 상위 5개 · 4단계 매도", inds, 5, (2,), (4,)),
                ("섹터+산업 ETF · 2단계 RS 상위 5개 · 3·4단계 매도", inds, 5, (2,), (3, 4))]
    for lab, tk, n, en, ex_ in etf_rows:
        e_, ntr, pin = etf_timing(dfs, tk, cal, n, en, ex_, bench=bench_s)
        cst = stats_curve(e_)
        buckets.append(("[ETF] " + lab, dict(n=ntr, win=pin, avgR=cst.get("cagr", np.nan), medR=np.nan, avgW=np.nan, avgL=np.nan, pf=np.nan,
                                             big=np.nan, weeks=np.nan, ret=cst.get("mdd", np.nan))))
        print(f"  [ETF] {lab}: 연평균 {f_(cst.get('cagr'))}% · 최대낙폭 {f_(cst.get('mdd'))}% · 투자 {pin:.0f}%")
    fill = np.mean(es["Cfill"]) * 100 if es["Cfill"] else 0
    es_row("[진입] 돌파 100%", es["A"])
    es_row("[진입] 돌파 50% + 되돌림 50% (못 사면 반은 현금)", es["B"])
    es_row(f"[진입] 되돌림 100% (13주 안 돌파선+3% 되돌림, 체결 {fill:.0f}%·못 사면 0)", es["C"])
    es_row("[진입] 되돌림 100% · 체결된 것만", [p for p, ok in zip(es["C"], es["Cfill"]) if ok])
    es_row("[추매] 10주선 터치 (수익 +5%·RS>0·10주선 상승 중)", es["add10"])
    es_row("[추매] 30주선 터치 (수익 +5%)", es["add30"])
    for b in EXT_BINS:
        pr = exs[b]
        sw = np.mean([x[2] for x in pr]) if pr else 0
        es_row(f"[이격] 30주선 {b[0]:+d}~{b[1]:+d}% (평균 손절폭 {sw:.1f}%)", [(x[0], x[1]) for x in pr])
    for lab, m in [("[진행도] 30주선 상승 20주 이하", allsig["upw"] <= 20), ("[진행도] 21~39주", (allsig["upw"] > 20) & (allsig["upw"] < 40)),
                   ("[진행도] 40주 이상", allsig["upw"] >= 40), ("[진행도] 52주 +60% 이상", allsig["rise"] >= 60),
                   ("[진행도] 후반 아님", ~late), ("[진행도] 후반", late),
                   ("[기저] 15주 미만", allsig["bl"] < 15), ("[기저] 15~25주", (allsig["bl"] >= 15) & (allsig["bl"] < 26)), ("[기저] 26주 이상", allsig["bl"] >= 26),
                   ("[신호] 돌파 · 기저 26주+", (allsig["signal"] == "돌파") & (allsig["bl"] >= 26)), ("[신호] 돌파 · 기저 26주 미만", (allsig["signal"] == "돌파") & (allsig["bl"] < 26)),
                   ("[신호] 풀백 · 후반", (allsig["signal"] == "풀백") & late), ("[신호] 풀백 · 후반 아님", (allsig["signal"] == "풀백") & ~late),
                   ("[추세] 풀백 · 30주선 13주 +3% 미만", pb & (allsig["up13"] < 3)), ("[추세] 풀백 · 30주선 13주 +3% 이상", pb & (allsig["up13"] >= 3)),
                   ("[추세] 풀백 · 최근 13주 30주선 -3% 이탈", pb & (allsig["mr13"] < 0.97)), ("[추세] 풀백 · 이탈 없음", pb & (allsig["mr13"] >= 0.97)),
                   ("[패턴] 돌파 · 역헤드앤숄더", bk & (allsig["ihs"] > 0)), ("[패턴] 돌파 · 그 외", bk & (allsig["ihs"] == 0)),
                   ("[패턴] 헤드앤숄더 천장 뒤 신호", allsig["hst"] > 0), ("[패턴] 천장 패턴 없음", allsig["hst"] == 0)] + [
                   ("[종류] 1→2단계 돌파 (대·중형)", (allsig["kind"] == "B12") & ~allsig["sm"]), ("[종류] 2단계 중간 돌파 (대·중형)", (allsig["kind"] == "B2") & ~allsig["sm"]),
                   ("[재돌파] 첫 돌파 (대·중형)", allsig["kind"].isin(["B12", "B2"]) & ~allsig["rebreak"].fillna(False).astype(bool) & ~allsig["sm"]),
                   ("[재돌파] 손절 뒤 13주 안 재돌파 (대·중형)", allsig["kind"].isin(["B12", "B2"]) & allsig["rebreak"].fillna(False).astype(bool) & ~allsig["sm"]),
                   ("[들락날락] 52주 30주선 이탈 0~1번 (대·중형)", (allsig["x52"] <= 1) & ~allsig["sm"]),
                   ("[들락날락] 2~3번", (allsig["x52"] >= 2) & (allsig["x52"] <= 3) & ~allsig["sm"]),
                   ("[들락날락] 4번 이상", (allsig["x52"] >= 4) & ~allsig["sm"]),
                   ("[급락] 13주 안 거래량 동반 급락 있음 (대·중형)", (allsig["dump"] > 0) & ~allsig["sm"]),
                   ("[급락] 없음", (allsig["dump"] == 0) & ~allsig["sm"])] + [
                   (f"[거래량] 돌파 매수 주 거래량 {lab} (대·중형)", allsig["kind"].isin(["B12", "B2"]) & ~allsig["sm"] & (allsig["evr"] >= lo) & (allsig["evr"] < hi))
                   for lab, lo, hi in (("1배 미만", 0, 1), ("1~1.5배", 1, 1.5), ("1.5~2배", 1.5, 2), ("2~3배", 2, 3), ("3배 이상", 3, 999))] + [
                   ("[종류] 30주선 풀백 (대·중형)", (allsig["kind"] == "MA") & ~allsig["sm"]), ("[종류] 돌파선 되돌림 · 30주선 아래 손절 (대·중형)", (allsig["kind"] == "RT") & ~allsig["sm"])] + [
                   (f"[매물] {nm} · 52주 고점 대비 {lab}", sel & (allsig["dd"] > lo) & (allsig["dd"] <= hi))
                   for nm, sel in (("풀백", pb & ~allsig["sm"]), ("돌파", bk & ~allsig["sm"]), ("소형주 전체", allsig["sm"]), ("대·중형 전체", ~allsig["sm"]))
                   for lab, lo, hi in (("0~-15%", -15, 1), ("-15~-25%", -25, -15), ("-25% 이하", -999, -25))] + [
                   ("[매물] 소형주 중 -25% 이하 비율 → 신호 수만 비교", allsig["sm"] & (allsig["dd"] <= -25)), ("[매물] 대·중형 중 -25% 이하 → 신호 수만 비교", ~allsig["sm"] & (allsig["dd"] <= -25))]:
        buckets.append((lab, stats_trades(allsig[m])))
        print(f"  {lab}: {stats_trades(allsig[m]).get('n', 0)}개 · 평균 {f_(stats_trades(allsig[m]).get('avgR'))}R")
    try:
        for n_, sp in ((10, 5.0), (10, 8.0), (60, 5.0), (60, 8.0), (30, 5.0), (30, 8.0), (40, 5.0)):
            mt = ma_touch_study(frames, start, capmap, n=n_, stop_pct=sp)
            if not len(mt):
                continue
            lg = ~mt["sm"]
            for lab, m in [("대·중형 전체", lg), ("소형", mt["sm"]),
                           ("대·중형 · 그 주 종가가 30주선 아래", lg & mt["u30"]), ("대·중형 · 30주선 위 유지", lg & ~mt["u30"]),
                           ("대·중형 · 2단계", lg & (mt["st"] == 2)), ("대·중형 · 3단계", lg & (mt["st"] == 3)),
                           ("대·중형 · RS 0+", lg & (mt["rs"] >= 0)), ("대·중형 · RS 0 미만", lg & (mt["rs"] < 0)),
                           ("대·중형 · ~2015", lg & (mt["sig_date"] < pd.Timestamp("2016-01-01"))), ("대·중형 · 2016~", lg & (mt["sig_date"] >= pd.Timestamp("2016-01-01")))]:
                st_ = stats_trades(mt[m])
                nm = f"[{n_}주선 터치 · 손절 -{sp:.0f}%] {lab}"
                buckets.append((nm, st_))
                print(f"  {nm}: {st_.get('n', 0)}개 · 승률 {f_(st_.get('win'))}% · 평균 {f_(st_.get('ret'))}% · {f_(st_.get('avgR'), 2)}R · 3R+ {f_(st_.get('big'))}% · {f_(st_.get('weeks'), 0)}주")
    except Exception as ex:
        print("  이평선 터치 분석 실패:", ex)
    rc = reclaim_study(frames, start, capmap)
    if len(rc):
        for mode, nm in (("close", "회복한 주 금요일 종가 매수"), ("intra", "그 주 장중 30주선 돌파 매수")):
            r0 = rc[rc["mode"] == mode]
            sw = r0["stop_pct"].mean() if len(r0) else 0
            for lab, m in [(f"{nm} · 전체 (평균 손절폭 {sw:.1f}%)", r0["k"] > 0),
                           ("· 이탈 1~2주", r0["k"] <= 2), ("· 이탈 3~4주", (r0["k"] >= 3) & (r0["k"] <= 4)), ("· 이탈 5~8주", r0["k"] >= 5),
                           ("· 회복 때 30주선 상승 중", r0["up"]), ("· 회복 때 30주선 평평·하락", ~r0["up"]),
                           ("· RS 0 이상", r0["rs"] >= 0), ("· RS 0 미만", r0["rs"] < 0),
                           ("· 대·중형", ~r0["sm"]), ("· 소형", r0["sm"]),
                           ("· ~2015", r0["sig_date"] < pd.Timestamp("2016-01-01")), ("· 2016~", r0["sig_date"] >= pd.Timestamp("2016-01-01"))]:
                st_ = stats_trades(r0[m])
                buckets.append(("[30주선 회복] " + lab, st_))
                print(f"  [30주선 회복] {nm if lab.startswith('·') else ''}{lab}: {st_.get('n', 0)}개 · 승률 {f_(st_.get('win'))}% · 평균 {f_(st_.get('ret'))}% · {f_(st_.get('avgR'), 2)}R")
    buckets.extend(beta_rows)
    buckets.extend(rtrows)
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
                 comp=comp, best=best, exit_curves=exit_curves, seg=seg, improve=improve, imp_curves=imp_curves, imp_base=ib, qqq_entry=qqq_entry, lev=lev)
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

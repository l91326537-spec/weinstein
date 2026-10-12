# -*- coding: utf-8 -*-
"""
앱 데이터 만들기 (GitHub Actions가 매일 자동 실행)
- screener.compute()로 시장·업종·종목을 계산하고
- docs/data/summary.json  : 시장, 업종, 후보, 전체 종목 목록
- docs/data/t/<티커>.json : 종목별 최근 3년 주봉(+30주선, RS)과 점수
"""
import datetime as dt, json, math, os, re, sys

import numpy as np
import pandas as pd

import screener as S

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "docs", "data")
TDIR = os.path.join(DATA, "t")
EXTRA = ["SPY", "QQQ", "KRW=X"]


def r2(x, d=2):
    if x is None:
        return None
    try:
        if isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x)):
            return None
        return round(float(x), d)
    except Exception:
        return None


def safe(t):
    return re.sub(r"[^A-Za-z0-9._-]", "_", t)


def week_complete(now=None):
    """미국 금요일 장 마감 이후~주말이면 이번 주봉 확정"""
    now = now or dt.datetime.now(dt.timezone.utc)
    wd, h = now.weekday(), now.hour
    return wd in (5, 6) or (wd == 4 and h >= 21)


def series_payload(df, bench, weeks=156):
    f = S.indicators(df)
    rs = S.mansfield_series(f["close"], bench) if bench is not None else pd.Series(np.nan, index=f.index)
    f = f.iloc[-weeks:]
    rs = rs.reindex(f.index)
    vol = df["Volume"].reindex(f.index)
    out = []
    for (d, r), v, m in zip(f.iterrows(), vol, rs):
        out.append([d.strftime("%Y-%m-%d"), r2(r["open"]), r2(r["high"]), r2(r["low"]), r2(r["close"]),
                    int(v) if pd.notna(v) else 0, r2(r["ma30"]), r2(m, 1)])
    return out


def buy_plan(r):
    """돌파 매수가·추격 한도·손절, 풀백 매수 구간·손절"""
    bs, chase = S.CFG["breakout_stop_pct"], S.CFG["breakout_chase_pct"]
    c, res, bl, ma = r["close"], r.get("res"), r.get("brk_level"), r.get("ma30")
    plan = {}
    if res and c <= res:
        b = res * 1.005
        plan.update(bkStatus="돌파 대기", bkBuy=r2(b), bkMax=r2(res * (1 + chase / 100)), bkStop=r2(b * (1 - bs / 100)))
    elif bl:
        b = bl * 1.005
        ok = c <= bl * (1 + chase / 100)
        plan.update(bkStatus="돌파함 · 매수 가능" if ok else "추격 한도 넘음", bkBuy=r2(b), bkMax=r2(bl * (1 + chase / 100)),
                    bkStop=r2((c if ok else b) * (1 - bs / 100)))
    # 잔고점 돌파: 30주선 우상향·30주선 위, 전고점(26주 고점)보다 5%+ 낮은 최근 8주 고점 바로 아래
    h8 = r.get("hi8")
    if h8 and res and ma and r["stage"] == 2 and (r.get("slope") or 0) > S.CFG["flat_pct"] and c > ma and h8 < res * 0.95 and h8 * 0.93 <= c < h8 and (r.get("ext") or 0) <= 15:
        mb = h8 * 1.005
        plan.update(mhBuy=r2(mb), mhStop=r2(min(r.get("low3") * 0.99, mb * 0.97)) if r.get("low3") else r2(mb * 0.95), mhMajor=r2(res))
    small = r.get("cap") == "소형" and r.get("atr10")
    if small:      # 소형주: 손절을 'ATR×2'까지 넓힘 (백테스트상 -5%는 흔들림에 자주 털림). 수량은 앱이 손절폭에 맞춰 줄임
        a2 = 2 * r["atr10"]
        if plan.get("bkBuy"):
            base = plan["bkStop"] / (1 - bs / 100)
            plan["bkStop"] = r2(min(plan["bkStop"], base - a2))
        plan.update(small=True, atr=r2(r["atr10"]))
    if r["stage"] in (1, 2) and ma:
        lo, hi = ma, ma * 1.05
        if bl and ma * 1.05 < bl * 0.97 < ma * 1.25:
            plan["rtZone"] = [r2(bl * 0.97), r2(bl * 1.03)]          # 돌파 지점 되돌림 구간
        in_pb = r.get("signal") == "풀백"
        pstop = S.entry_stop(r, "풀백") if in_pb else ma * 0.97
        if small:
            pstop = min(pstop, (c if in_pb else hi) - 2 * r["atr10"])
        plan.update(pbZone=[r2(lo), r2(hi)], pbStop=r2(pstop), pbNow=in_pb)
    return plan


def stock_payload(r):
    pz = None
    if r["stage"] == 2 and r.get("ma30"):
        pz = [r2(r["ma30"]), r2(max(r["ma30"] * 1.05, r.get("brk_level") or 0))]
    d = dict(score=r["score"], stage=r["stage"], label=r["stage_label"], signal=r["signal"],
             close=r2(r["close"]), ma30=r2(r["ma30"]), ext=r2(r["ext"], 1), slope=r2(r["slope"], 1),
             res=r2(r["res"]), buyStop=r2(r["buy_stop"]), pullback=pz, stop=r2(r["stop"]),
             stopPct=r2(r["stop_pct"], 1), rs=r2(r["rs"], 1), vr=r2(r["vr"], 1), brkVr=r2(r.get("brk_vr"), 1),
             volGrade=r["volGrade"], rsGrade=r["rsGrade"], breakdown=r["breakdown"], caps=r["caps"],
             sectorStage=r.get("sector_stage"), kind=r.get("kind") or "")
    d.update(buy_plan(r))
    return d


def main(fetch=None, universe=None):
    R = S.compute(fetch=fetch or S.fetch_weekly, universe=universe, extra=EXTRA)
    dfs, rows, uni, bench = R["dfs"], R["rows"], R["uni"], R["bench"]
    got = sum(1 for t in uni if t in dfs)
    if "^GSPC" not in dfs or got < len(uni) * 0.5:
        sys.exit(f"데이터를 충분히 받지 못했습니다 ({got}/{len(uni)}). 이전 앱 화면을 그대로 둡니다.")
    os.makedirs(TDIR, exist_ok=True)
    byt = {r["ticker"]: r for r in rows}
    # 종목별 파일
    names = {**{v: S.SECTOR_KO[k] + " 섹터" for k, v in S.SECTOR_ETF.items()}, **S.INDUSTRY_ETF,
             "SPY": "S&P500 ETF", "QQQ": "나스닥100 ETF", "^GSPC": "S&P500", "^IXIC": "나스닥"}
    index = []
    written = set()
    for t, df in dfs.items():
        info = uni.get(t, {})
        b = bench.get(info.get("bench", "us")) if bench else None
        if (t.startswith("^") and t not in S.MARKET) or "=" in t:
            continue
        if t in uni and t not in byt and not info.get("mine"):
            continue          # 거래대금·가격 기준에서 걸러진 종목은 앱 데이터에서 뺌
        payload = dict(t=t, name=str(info.get("name") or names.get(t, t)), cap=info.get("cap"),
                       sector=info.get("mysector") or S.SECTOR_KO.get(info.get("sector") or "", None),
                       w=series_payload(df, b))
        if t in byt:
            payload.update(stock_payload(byt[t]))
        else:
            st = S.stage_of(df)
            if st:
                payload.update(stage=st["stage"], label=st["stage_label"], close=r2(st["close"]),
                               ma30=r2(st["ma30"]), ext=r2(st["ext"], 1))
        with open(os.path.join(TDIR, safe(t) + ".json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        written.add(safe(t))
        c = df["Close"]
        index.append(dict(t=t, f=safe(t), n=payload["name"], s=payload.get("sector"), cap=info.get("cap"),
                          sc=payload.get("score"), st=payload.get("stage"), sg=payload.get("signal") or "",
                          c=r2(c.iloc[-1]), w1=r2((c.iloc[-1] / c.iloc[-2] - 1) * 100, 1) if len(c) > 1 else None,
                          k="etf" if t in names else "stock"))
    # 오래된 종목 파일 정리
    for fn in os.listdir(TDIR):
        if fn.endswith(".json") and fn[:-5] not in written:
            os.remove(os.path.join(TDIR, fn))
    # 요약
    mk = {t: (dict(name=S.MARKET[t], stage=v["stage"], label=v["stage_label"], ext=r2(v["ext"], 1),
                   slope=r2(v["slope"], 1), close=r2(v["close"])) if v else None) for t, v in R["mk"].items()}
    secs = []
    for t, v in R["sectors"].items():
        members = [r for r in rows if r.get("sector_etf") == t]
        secs.append(dict(etf=t, f=safe(t), name=v["name"], stage=v["stage"], label=v["stage_label"],
                         ext=r2(v["ext"], 1), slope=r2(v["slope"], 1), rs=r2(v["rs"], 1),
                         kind="sector" if t in S.SECTOR_ETF.values() else "industry",
                         top=[dict(t=m["ticker"], sc=m["score"]) for m in members[:3]]))
    order = {2: 0, 1: 1, 3: 2, 4: 3}
    secs.sort(key=lambda s: (order.get(s["stage"], 4), -(s["rs"] if s["rs"] is not None else -99)))
    cands = []
    for r in rows:
        if not r["signal"] and r["score"] < 65:
            continue
        d = stock_payload(r)
        d.update(t=r["ticker"], f=safe(r["ticker"]), n=str(r["name"]), s=r.get("sector_ko"), cap=r.get("cap"), mine=r.get("mine"))
        cands.append(d)
    fx = r2(dfs["KRW=X"]["Close"].iloc[-1], 1) if "KRW=X" in dfs else None
    summary = dict(fx=fx, updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), day=R["day"],
                   weekComplete=week_complete(), lastBar=str(dfs["^GSPC"].index[-1].date()) if "^GSPC" in dfs else None,
                   market=mk, worst=R["worst"], sectors=secs, candidates=cands,
                   index=sorted(index, key=lambda x: x["t"]),
                   counts=dict(total=len(rows), universe=len(uni), **{c: sum(1 for r in rows if r.get("cap") == c) for c in ("대형", "중형", "소형")}))
    with open(os.path.join(DATA, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"앱 데이터 완료: 종목 파일 {len(written)}개 · 후보 {len(cands)}개")


if __name__ == "__main__":
    main()

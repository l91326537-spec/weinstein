# -*- coding: utf-8 -*-
"""
와인스테인 주봉 스크리너
- S&P500 + 나스닥100 전 종목(+ my_tickers.txt)을 주봉으로 받아
  30주선 단계, 맨스필드 RS, 거래량, 베이스 돌파를 계산하고 점수를 매깁니다.
- 결과: reports/report_YYYY-MM-DD.html, results_YYYY-MM-DD.csv, board_import_YYYY-MM-DD.json
실행: python screener.py            (전체)
      python screener.py --quick    (나스닥100 + 내 종목만, 빠른 확인용)
"""
import argparse, datetime as dt, html, io, json, math, os, sys, time

import numpy as np
import pandas as pd

try:  # 윈도우 창에 진행 상황이 바로바로 보이게
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports")

# ---------------- 설정 (원하면 숫자만 바꾸세요) ----------------
CFG = {
    "slope_weeks": 10,       # 30주선 기울기를 볼 기간(주)
    "flat_pct": 0.8,         # 10주간 30주선 변화가 ±이 % 안이면 '평평'
    "whipsaw": 3,            # 최근 15주 동안 30주선을 이 횟수 이상 넘나들면 방향 미정(1·3단계)
    "base_weeks": 26,        # 저항선(베이스 상단)을 찾을 기간(주)
    "breakout_recent": 3,    # 최근 몇 주 안의 돌파를 '돌파'로 볼지
    "overheat_pct": 20.0,    # 30주선 대비 이 % 이상이면 과열
    "stop_risk_pct": 15.0,   # 손절폭 이 % 초과면 위험
    "stop_lookback": 10,     # 손절 기준 저점을 찾을 기간(주)
    "min_price": 5.0,        # 이 가격 미만 종목 제외
    "late_weeks": 40,        # 30주선이 이 주 수 넘게 계속 오르면 2단계 후반
    "late_rise": 60.0,       # 30주선이 52주 최저보다 이 % 넘게 오르면 2단계 후반
    "late_pts": 8,           # 2단계 후반 감점
    "base_bonus": 4,         # 기저 26주 이상 돌파 가점
    "base_short": 8,         # 기저 26주 미만 돌파 감점
    "use_late": True,        # 2단계 후반 감점 (백테스트 결과로 결정)
    "use_trend": True,       # 풀백: 30주선 기울기 약함·최근 이탈 감점 (백테스트 결과로 결정)
    "use_pattern": True,     # 역헤드앤숄더 돌파 가점·헤드앤숄더 천장 감점 (백테스트 결과로 결정)
    "use_overhead": False,   # 52주 고점 대비 많이 빠진 종목 감점 (백테스트 결과로 결정)
    "overhead_all": False,   # True면 돌파에도 적용, False면 풀백만
    "use_chop": False,       # 30주선을 자주 들락날락하는 종목 감점 (백테스트 결과로 결정)
    "use_dump": False,       # 큰 거래량 동반 급락 이력 감점 (백테스트 결과로 결정)
    "no_sector_pts": 10,     # 업종 ETF를 못 찾은 종목은 업종 점수를 중립으로
    "squeeze_pts": 0,        # 볼린저 수축(최근 8주 안에 밴드 폭이 52주 하위 25%) 뒤 돌파·풀백 가점. 신호는 2.23R vs 1.95R로 낫지만
                             # 포트폴리오에서 +3·+5·+8 모두 수익 개선 없이 최대 낙폭만 커져(+5: -27% → -33%) 끔
    "b12_pts": 10,           # 1→2단계 돌파 가점 (먼저 사도록)
    "use_base": True,        # 기저 길이 가감점 (백테스트 결과로 결정)
    "min_dollar_vol": 20e6,  # 주간 거래대금(달러) 평균이 이보다 작으면 제외 (거래 얇은 종목)
    "breakout_stop_pct": 5.0,  # 돌파 매수 시 손절폭 (매수가 대비 %)
    "breakout_chase_pct": 7.0, # 돌파 지점보다 이 % 넘게 올라가 있으면 '돌파 매수'로 보지 않음 (추격 금지)
    "min_stop_pct": 3.0,       # 손절은 매수가보다 최소 이만큼 아래
}

SECTOR_ETF = {  # GICS 섹터 → SPDR 섹터 ETF
    "Information Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Communication Services": "XLC", "Industrials": "XLI",
    "Consumer Staples": "XLP", "Energy": "XLE", "Utilities": "XLU", "Real Estate": "XLRE",
    "Materials": "XLB",
}
SECTOR_KO = {
    "Information Technology": "정보기술", "Health Care": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "경기소비재", "Communication Services": "커뮤니케이션",
    "Industrials": "산업재", "Consumer Staples": "필수소비재", "Energy": "에너지",
    "Utilities": "유틸리티", "Real Estate": "부동산", "Materials": "소재",
}
INDUSTRY_ETF = {  # 세부 업종 ETF (업종 단계 표에 표시)
    "SMH": "반도체", "IGV": "소프트웨어", "FDN": "인터넷", "ARKF": "핀테크", "CIBR": "사이버보안",
    "BOTZ": "로봇·자율주행", "XBI": "바이오(중소형)", "IHI": "의료기기", "KRE": "지역은행", "IAI": "증권·거래소",
    "ITA": "항공우주·방산", "JETS": "항공", "XHB": "주택건설", "XRT": "소매", "TAN": "태양광",
    "URA": "원자력·우라늄", "XME": "금속·광업", "GDX": "금광", "OIH": "석유서비스", "IYT": "운송·물류",
    "PAVE": "인프라", "KWEB": "중국 인터넷", "IBB": "바이오", "SKYY": "클라우드",
    # 보드에서 쓰던 업종 ETF
    "MOO": "농업", "KBE": "은행", "MSOS": "대마초", "FXI": "중국", "COPX": "구리·원자재", "BKCH": "크립토",
    "IHF": "헬스케어 서비스·보험", "ITB": "주택건설(건설사)", "PEJ": "레저·여행", "PPH": "제약", "SLX": "철강",
    "QTUM": "양자컴퓨팅", "REMX": "희토류", "EATZ": "외식", "BDRY": "해운",
}
# 내 업종 이름 → 그 업종을 대표하는 ETF (my_tickers.txt의 업종명으로 업종 단계를 계산)
MYSECTOR_ETF = {
    "반도체": "SMH", "소프트웨어": "IGV", "인터넷": "FDN", "핀테크": "ARKF", "사이버보안": "CIBR",
    "로봇·자율주행": "BOTZ", "바이오": "IBB", "의료기기": "IHI", "클라우드": "SKYY", "농업": "MOO",
    "구리·원자재": "COPX", "금속·광업": "XME", "금광": "GDX", "에너지": "XLE", "철강": "SLX",
    "헬스케어 서비스·보험": "IHF", "제약": "PPH", "레저·여행": "PEJ", "외식": "EATZ", "광통신": "SMH",
    "IT 하드웨어": "XLK", "전자부품·EMS·유통": "XLK", "산업재": "XLI", "미디어·통신": "XLC",
    "양자컴퓨팅": "QTUM", "해운·탱커": "BDRY", "원자력·우라늄": "URA", "교육": "XLY",
    "생명과학 도구": "XLV", "헬스케어 테크": "XLV", "화장품·생활용품": "XLP", "중국": "FXI", "중국 교육": "FXI",
}
CAP_ORDER = {"대형": 0, "중형": 1, "소형": 2}
SECTOR_FIX = {"ASML": "Information Technology", "MSTR": "Information Technology", "ALNY": "Health Care",
              "MELI": "Consumer Discretionary", "SHOP": "Information Technology", "CCEP": "Consumer Staples",
              "PDD": "Consumer Discretionary", "RKLB": "Industrials", "ARM": "Information Technology",
              "TRI": "Industrials", "FER": "Industrials", "ALAB": "Information Technology", "CRWV": "Information Technology"}
UNI_DIR = os.path.join(HERE, "universe")
MARKET = {"^IXIC": "나스닥", "^GSPC": "S&P500"}
BENCH = {"us": "^GSPC", "ks": "^KS11", "kq": "^KQ11"}


# ---------------- 종목 목록 ----------------
def _read_html(url):
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (screener)"}, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def _wiki_index(url, min_rows):
    """위키피디아 지수 구성 종목 표 → [(ticker, name, gics_sector)]"""
    tables = _read_html(url)
    for t in tables:
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = [str(c[-1]) for c in t.columns]
        t.columns = [str(c).split("[")[0].strip() for c in t.columns]
        tcol = next((c for c in t.columns if str(c) in ("Symbol", "Ticker", "Ticker symbol")), None)
        scol = next((c for c in t.columns if "GICS" in str(c) and "Sector" in str(c)), None) \
            or next((c for c in t.columns if "Industry" in str(c) and "Sub" not in str(c)), None)
        if tcol is None or scol is None or len(t) < min_rows:
            continue
        ncol = next((c for c in t.columns if str(c) in ("Security", "Company")), tcol)
        return [(str(r[tcol]).replace(".", "-").strip(), str(r[ncol]), ICB_TO_GICS.get(str(r[scol]).strip(), str(r[scol]).strip()))
                for _, r in t.iterrows()]
    raise ValueError("표를 찾지 못함: " + " / ".join(f"{len(t)}행[{','.join(map(str, t.columns))[:80]}]" for t in tables[:8]))


ICB_TO_GICS = {  # 위키 나스닥100 표가 ICB 분류를 쓰는 경우 GICS 이름으로 맞춤
    "Technology": "Information Technology", "Telecommunications": "Communication Services",
    "Basic Materials": "Materials", "Health Care": "Health Care", "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples", "Industrials": "Industrials", "Energy": "Energy",
    "Utilities": "Utilities", "Real Estate": "Real Estate", "Financials": "Financials",
}


def _russell2000():
    """러셀 2000: iShares IWM 보유 종목 파일"""
    import csv, requests
    urls = ["https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/latest-holdings.csv",
            "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax"
            "?fileType=csv&fileName=IWM_holdings&dataType=fund"]
    lines, k = None, None
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=60)
            r.raise_for_status()
            ls = r.text.lstrip("\ufeff").splitlines()
            k = next(i for i, l in enumerate(ls) if l.startswith("Ticker,"))
            lines = ls
            break
        except Exception as e:
            print("   러셀2000 주소 실패:", url[:70], e)
    if lines is None:
        raise ValueError("러셀2000 보유 종목 파일을 받지 못함")
    fix = {"Communication": "Communication Services"}
    out = []
    for row in csv.DictReader(lines[k:]):
        tk = (row.get("Ticker") or "").strip()
        if row.get("Asset Class") != "Equity" or not tk or not all(ch.isalpha() or ch in ".-" for ch in tk):
            continue
        sec = (row.get("Sector") or "").strip()
        out.append((tk.replace(".", "-"), (row.get("Name") or tk).strip().title(), fix.get(sec, sec)))
    if len(out) < 1000:
        raise ValueError(f"종목 수가 너무 적음 ({len(out)})")
    return out


def _nasdaq100_api():
    import requests
    r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    rows = r.json()["data"]["data"]["rows"]
    out = [(str(x["symbol"]).replace(".", "-").strip(), str(x.get("companyName") or x["symbol"]), "") for x in rows]
    if len(out) < 90:
        raise ValueError(f"종목 수가 너무 적음 ({len(out)})")
    return out


def _first_ok(*fns):
    err = None
    for fn in fns:
        try:
            return fn()
        except Exception as e:
            err = e
    raise err


SOURCES = [  # (파일 이름, 크기 구분, 받는 함수)
    ("sp500", "대형", lambda: _wiki_index("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 400)),
    ("ndx", "대형", lambda: _first_ok(lambda: _wiki_index("https://en.wikipedia.org/wiki/Nasdaq-100", 90),
                                       _nasdaq100_api)),
    ("sp400", "중형", lambda: _wiki_index("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", 300)),
    ("r2000", "소형", _russell2000),
]


def refresh_universe():
    """종목 목록을 새로 받아 universe/*.csv로 저장 (GitHub에서 한 달에 한 번 실행)"""
    os.makedirs(UNI_DIR, exist_ok=True)
    log = [f"갱신 {dt.date.today()}"]
    for key, cap, fn in SOURCES:
        try:
            rows = fn()
            pd.DataFrame(rows, columns=["ticker", "name", "sector"]).to_csv(os.path.join(UNI_DIR, key + ".csv"), index=False)
            log.append(f"{key}: {len(rows)}종목 저장")
        except Exception as e:
            log.append(f"{key}: 받지 못함 ({type(e).__name__}: {e}) — 기존 목록 유지")
        print(log[-1])
    with open(os.path.join(UNI_DIR, "log.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")


def load_universe(quick=False):
    """ticker -> {"name","sector","bench","cap","mine"}"""
    uni = {}
    print("종목 목록 불러오는 중...")
    for key, cap, fn in SOURCES:
        if quick and key != "ndx":
            continue
        path = os.path.join(UNI_DIR, key + ".csv")
        rows = None
        if os.path.exists(path):
            rows = pd.read_csv(path).fillna("").values.tolist()
        else:
            try:
                rows = fn()
            except Exception as e:
                print(f"{key} 목록을 못 불러왔습니다:", e)
        for tk, name, sec in rows or []:
            tk = str(tk).strip()
            if not tk:
                continue
            cur = uni.get(tk)
            if cur is None or CAP_ORDER[cap] < CAP_ORDER[cur["cap"]]:
                uni[tk] = {"name": name, "sector": sec or (cur or {}).get("sector"), "bench": "us", "cap": cap}
        print(f"  {key}: {len(rows or [])}종목")
    for tk, sec in SECTOR_FIX.items():          # 나스닥100에만 있어 업종이 비는 종목
        if tk in uni and not uni[tk].get("sector"):
            uni[tk]["sector"] = sec
    # 내 종목 (한 줄에: 티커 [업종명])  예) 005930.KS 반도체 / OUST 로봇·자율주행
    path = os.path.join(HERE, "my_tickers.txt")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            tk = parts[0].upper()
            b = "ks" if tk.endswith(".KS") else "kq" if tk.endswith(".KQ") else "us"
            info = uni.get(tk, {"name": tk, "sector": None, "bench": b, "cap": "기타"})
            info["mine"] = True
            if len(parts) > 1:
                info["mysector"] = parts[1].strip()
            uni[tk] = info
    if not uni:
        sys.exit("종목 목록이 비어 있습니다. 인터넷 연결 또는 my_tickers.txt를 확인하세요.")
    print(f"총 {len(uni)}종목")
    return uni


# ---------------- 데이터 ----------------
def fetch_weekly(tickers, years=3, chunk=50):
    import yfinance as yf
    out = {}
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        print(f"  주봉 받는 중 {i + 1}~{i + len(part)} / {len(tickers)}")
        for attempt in range(3):
            try:
                start = (dt.date.today() - dt.timedelta(days=int(365.25 * years))).isoformat()
                data = yf.download(part, start=start, interval="1wk", group_by="ticker",
                                   auto_adjust=True, threads=True, progress=False, timeout=30)
                break
            except Exception as e:
                print("   재시도:", e)
                time.sleep(3)
        else:
            print("   이 묶음은 건너뜁니다 (야후 응답 없음)")
            continue
        time.sleep(1)  # 야후 요청 제한 피하기
        got = 0
        for t in part:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                if len(df) >= 60:
                    out[t] = df
                    got += 1
            except Exception:
                pass
        print(f"   → {got}/{len(part)}개 받음")
        if got == 0 and i == 0:
            print("   ※ 첫 묶음을 하나도 못 받았습니다. 야후가 요청을 막았을 수 있어요. 몇 분 뒤 다시 실행해 보세요.")
    return out


# ---------------- 계산 ----------------
def indicators(df):
    """모든 주에 대해 지표를 계산 (각 주는 그 주까지의 데이터만 사용 → 백테스트에도 그대로 씀)"""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    sw, bw = CFG["slope_weeks"], CFG["base_weeks"]
    ma = c.rolling(30).mean()
    f = pd.DataFrame(index=df.index)
    f["open"], f["high"], f["low"], f["close"] = df["Open"], h, l, c
    f["ma30"] = ma
    f["slope"] = (ma / ma.shift(sw) - 1) * 100
    f["prior"] = (ma / ma.shift(26) - 1) * 100
    f["ma_peak"] = ma.rolling(100, min_periods=1).max()
    above = c > ma
    f["above"] = above
    f["crosses"] = above.astype(int).diff().abs().rolling(15).sum()
    res = h.shift(1).rolling(bw).max()           # 지난 26주 고점(이번 주 제외) = 저항
    f["res"] = res
    f["base_low"] = l.shift(1).rolling(bw).min()
    vavg = v.shift(1).rolling(10).mean()
    vr = (v / vavg).where(vavg > 0, 0.0)
    f["vr"] = vr
    brk = (c > res) & (c.shift(1) <= res)
    f["brk_week"] = np.nan
    f["brk_level"] = np.nan
    f["brk_vr"] = np.nan
    for k in range(CFG["breakout_recent"], 0, -1):   # 가장 최근 돌파가 남도록 먼 쪽부터 덮어씀
        m = brk.shift(k - 1, fill_value=False)
        f.loc[m, "brk_week"] = k
        f.loc[m, "brk_level"] = res.shift(k - 1)[m]
        f.loc[m, "brk_vr"] = vr.shift(k - 1)[m]
    chg = c.diff()
    up = v.where(chg > 0, 0.0).rolling(10).sum()
    dn = v.where(chg < 0, 0.0).rolling(10).sum()
    f["updn"] = (up / dn).where(dn > 0, 3.0)
    f["hi_all"] = h.rolling(156, min_periods=1).max()
    f["ext"] = (c / ma - 1) * 100
    f["stop"] = l.rolling(CFG["stop_lookback"]).min() * 0.99
    f["stop_pct"] = (f["stop"] / c - 1) * 100
    f["low8"] = l.rolling(8).min()
    f["low3"] = l.rolling(3).min()
    run = above.astype(int).groupby((~above).cumsum()).cumsum()
    f["weeks_above"] = run.clip(upper=30)
    f["dollar_vol"] = (c * v).rolling(10).mean()
    pc = c.shift(1)
    f["atr10"] = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).rolling(10).mean()   # 주간 평균 변동폭
    # 기저 길이: 주가 범위 40% 이내 + 30주선 변화 ±10% 이내로 버틴 가장 긴 구간(주). 최근 36주 안에 끝난 기저만 인정
    bl = pd.Series(0.0, index=f.index)
    for n in (10, 15, 20, 26, 39, 52, 78):
        ok = (h.rolling(n).max() / l.rolling(n).min() <= 1.40) & ((ma / ma.shift(n - 1) - 1).abs() <= 0.10)
        bl = bl.where(~ok, np.maximum(bl, n))
    f["base_len"] = bl.rolling(36, min_periods=1).max()
    # 2단계가 얼마나 오래·많이 진행됐나: 30주선이 연속 상승한 주 수, 52주 최저 30주선 대비 상승률
    up = (f["slope"] > CFG["flat_pct"]).astype(int)
    f["ma_up_weeks"] = up.groupby((up == 0).cumsum()).cumsum()
    f["ma_rise52"] = (ma / ma.rolling(52, min_periods=20).min() - 1) * 100
    # 인수합병 의심: 최근 1년 안에 한 주 +12% 이상 급등 후, 8주 범위 7% 이내·변동폭이 평소의 45% 이하로 '고정'
    atrp = f["atr10"] / c
    prev = atrp.shift(40).rolling(52, min_periods=20).median()       # 급등 전(약 1년 전) 평소 변동폭
    jump = (c / c.shift(1) - 1).rolling(52, min_periods=1).max() >= 0.12
    # 추세 품질: 30주선 13주 상승률, 최근 13주 동안 종가가 30주선 아래로 가장 많이 내려간 정도
    f["ma_up13"] = (ma / ma.shift(13) - 1) * 100
    f["dd52"] = (c / h.rolling(52, min_periods=20).max() - 1) * 100          # 52주 고점 대비 (위쪽 매물)
    f["hi8"] = h.rolling(8).max()
    f["crosses52"] = (above.shift(1, fill_value=False) & ~above).astype(int).rolling(52, min_periods=20).sum()   # 30주선 아래로 내려간 횟수
    dumpw = ((c / c.shift(1) - 1) <= -0.07) & (vr >= 2)                      # 거래량 2배+ 동반 -7% 이상 급락 주
    f["dump13"] = dumpw.astype(int).rolling(13, min_periods=1).max()                                             # 잔고점 (최근 8주 고점)
    # 최근 13주 안에 돌파했다가 돌파선 -5%까지 밀린 적(=돌파 매수였다면 손절)이 있으면 '돌파 실패'
    fb = np.zeros(len(f))
    brk_w = np.nonzero(brk.values)[0]
    resv, lv = res.values, l.values
    for b in brk_w:
        lvl = resv[b]
        if np.isnan(lvl):
            continue
        hit = None
        for j in range(b + 1, min(b + 14, len(f))):
            if lv[j] <= lvl * 0.95:
                hit = j
                break
        if hit is not None:
            fb[hit:min(b + 14 + 13, len(f))] = 1
    f["failed_brk"] = fb
    f["min_rel13"] = (c / ma).rolling(13).min()
    f["ihs"], f["hs_top"] = patterns(h, l, c)
    # 미너비니 트렌드 템플릿 (주봉판, 30주선≈150일선·40주선≈200일선): 6가지 중 몇 개 충족했나
    ma40 = c.rolling(40).mean()
    lo52, hi52 = l.rolling(52, min_periods=40).min(), h.rolling(52, min_periods=40).max()
    mv = [c > ma, c > ma40, ma > ma40, ma40 > ma40.shift(4), c >= lo52 * 1.3, c >= hi52 * 0.75]
    f["mv_n"] = sum(x.astype(int) for x in mv)
    f["minervini"] = (f["mv_n"] == len(mv)).astype(float)
    # 볼린저 수축: 최근 8주 안에 20주 밴드 폭이 지난 52주 중 하위 25%까지 좁아졌음
    bbw = 4 * c.rolling(20).std() / c.rolling(20).mean()
    f["squeeze"] = (bbw.rolling(8).min() <= bbw.rolling(52, min_periods=40).quantile(0.25)).astype(float)
    f["pinned"] = ((h.rolling(8).max() / l.rolling(8).min() - 1 <= 0.07) & (atrp <= 0.03) & (atrp <= prev * 0.45) & jump).astype(float)
    return f


def _pivots(x, k, low):
    """k주 양옆보다 낮은(높은) 주 = 바닥(꼭지). k주 뒤에야 확정됨"""
    r = x.rolling(2 * k + 1, center=True)
    m = (x == (r.min() if low else r.max())).values
    out = []
    for j in np.nonzero(m)[0]:
        if not out or j - out[-1] > k:          # 같은 값이 이어진 바닥·꼭지는 하나로
            out.append(int(j))
    return out


def patterns(h, l, c, k=3):
    """역헤드앤숄더 돌파(최근 3주 안), 헤드앤숄더 천장 완성(최근 8주 안). 각 주에는 그 주까지 확정된 꼭지·바닥만 사용"""
    n = len(c)
    ihs, hs = np.zeros(n), np.zeros(n)
    lows, highs = _pivots(l, k, True), _pivots(h, k, False)
    lv, hv, cv = l.values, h.values, c.values
    import bisect
    def last3(piv, i):
        m = bisect.bisect_right(piv, i - k)              # i주까지 확정된 꼭지·바닥 개수
        if m < 3 or i - piv[m - 3] > 60:
            return None
        return piv[m - 3:m]
    li = hi = 0
    for i in range(n):
        t = last3(lows, i)
        if t:
            a, b, d = t
            sh1, head, sh2 = lv[a], lv[b], lv[d]
            if d - a >= 8 and head < min(sh1, sh2) * 0.97 and abs(sh1 / sh2 - 1) <= 0.12:
                neck = hv[a:d + 1].max()
                for j in range(max(d + 1, i - 2), i + 1):          # 최근 3주 안에 목선 위로 종가 돌파
                    if cv[j] > neck and cv[j - 1] <= neck:
                        ihs[i] = 1
        t = last3(highs, i)
        if t:
            a, b, d = t
            sh1, head, sh2 = hv[a], hv[b], hv[d]
            if d - a >= 8 and head > max(sh1, sh2) * 1.03 and abs(sh1 / sh2 - 1) <= 0.12:
                neck = lv[a:d + 1].min()
                if any(cv[j] < neck for j in range(max(d + 1, i - 7), i + 1)):   # 최근 8주 안에 목선 아래로 이탈
                    hs[i] = 1
    return pd.Series(ihs, index=c.index), pd.Series(hs, index=c.index)


def late_stage(r):
    """2단계 후반: 30주선이 40주 넘게 계속 오르거나, 52주 최저 30주선보다 60% 넘게 오름 → 3단계 위험이 커지는 구간"""
    w, rise = r.get("ma_up_weeks"), r.get("ma_rise52")
    return bool((w is not None and not pd.isna(w) and w >= CFG["late_weeks"]) or (rise is not None and not pd.isna(rise) and rise >= CFG["late_rise"]))


def quality_adj(s, signal=None):
    """기저 품질·2단계 진행도·추세·패턴 가감점과 인수합병 의심 여부.
    백테스트(2006~, 신호 약 5만 개): 최근 30주선 이탈 뒤 풀백 1.33R vs 1.72R, 헤드앤숄더 천장 뒤 신호 0.78R vs 1.55R,
    역헤드앤숄더 돌파 1.39R vs 그 외 1.40R(차이 없음), 30주선 기울기 약한 풀백 1.60R vs 1.61R(차이 없음)
    백테스트(신호 5만여 개) 근거: 2단계 후반 신호 평균 1.44R vs 아닌 신호 1.62R, 돌파 중 기저 26주+ 1.54R vs 짧은 기저 1.10R"""
    out = dict(late=0, base=0, trend=0, pat=0, ovh=0, rebrk=0, chop=0, dump=0, pinned=bool(s.get("pinned")), notes=[])
    if out["pinned"]:
        out["notes"].append("인수합병 의심 (가격 고정) · 상한 20")
        return out
    if CFG["squeeze_pts"] and signal in ("돌파", "풀백") and s.get("stage") in (1, 2) and s.get("squeeze"):
        out["sqz"] = CFG["squeeze_pts"]
        out["notes"].append(f"볼린저 수축 뒤 신호 +{CFG['squeeze_pts']}")
    if s.get("stage") != 2:
        return out
    if late_stage(s):
        out["late"] = -CFG["late_pts"]
        out["notes"].append(f"2단계 후반 -{CFG['late_pts']} (30주선 {int(s.get('ma_up_weeks') or 0)}주째 상승 · 52주 +{(s.get('ma_rise52') or 0):.0f}%)")
    if CFG["use_trend"] and signal == "풀백":
        up13, mr = s.get("ma_up13"), s.get("min_rel13")
        if up13 is not None and not pd.isna(up13) and up13 < 3:
            out["trend"] = out.get("trend", 0) - 8; out["notes"].append(f"30주선 기울기 약함 (13주 {up13:+.1f}%) -8")
        if mr is not None and not pd.isna(mr) and mr < 0.97:
            out["trend"] = out.get("trend", 0) - 10; out["notes"].append(f"최근 13주 안에 30주선 이탈 ({(mr - 1) * 100:.0f}%) -10")
    if s.get("crosses52") is not None and not pd.isna(s.get("crosses52")) and s["crosses52"] >= 4:
        out["chop"] = -8
        if CFG["use_chop"]:
            out["notes"].append(f"30주선 들락날락 (52주 {int(s['crosses52'])}번 이탈) -8")
    if s.get("dump13"):
        out["dump"] = -10
        if CFG["use_dump"]:
            out["notes"].append("최근 13주 안 큰 거래량 동반 급락 -10")
    if signal == "돌파" and s.get("failed_brk"):
        out["rebrk"] = -20
        out["notes"].append("최근 13주 안 돌파 실패 뒤 재돌파 -20 (백테스트 0.19R, 승률 11%)")
    dd = s.get("dd52")
    if CFG["use_overhead"] and dd is not None and not pd.isna(dd) and (signal == "풀백" or CFG["overhead_all"]):
        if dd <= -25:
            out["ovh"] = -10; out["notes"].append(f"52주 고점 대비 {dd:.0f}% · 위쪽 매물 많음 -10")
        elif dd <= -15:
            out["ovh"] = -5; out["notes"].append(f"52주 고점 대비 {dd:.0f}% · 위쪽 매물 -5")
    if CFG["use_pattern"]:
        if s.get("ihs") and signal == "돌파":
            out["notes"].append("역헤드앤숄더 목선 돌파 (참고 · 백테스트상 가점 효과 없음)")
        if s.get("hs_top"):
            out["pat"] = out.get("pat", 0) - 10; out["notes"].append("헤드앤숄더 천장 완성 -10")
    if signal == "돌파":
        bl = s.get("base_len") or 0
        if bl >= 26:
            out["base"] = CFG["base_bonus"]; out["notes"].append(f"기저 {int(bl)}주 돌파 +{CFG['base_bonus']}")
        else:
            out["base"] = -CFG["base_short"]; out["notes"].append(f"기저 짧은 돌파 ({int(bl)}주) -{CFG['base_short']}")
    return out


def signal_kind(s, signal):
    """신호 세부 종류: 1→2 돌파 / 돌파(2단계 중간) / 30주선 풀백 / 돌파선 되돌림"""
    if signal == "돌파":
        lab = s.get("stage_label") or ""
        is12 = lab.startswith("1단계→2단계") or ((s.get("base_len") or 0) >= 15 and (s.get("ma_up_weeks") or 99) <= 8)
        return "1→2 돌파" if is12 else "돌파"
    if signal == "풀백":
        return "30주선 풀백" if -3 <= (s.get("ext") or 0) <= 5 else "돌파선 되돌림"
    return signal or ""


def apply_quality(score, q, use_late=True, use_base=True):
    if q["pinned"]:
        return min(score, 20)
    return int(max(0, min(100, score + (q["late"] if use_late else 0) + (q["base"] if use_base else 0) + q.get("trend", 0) + q.get("pat", 0) + q.get("ovh", 0) + q.get("rebrk", 0) + q.get("sqz", 0)
                               + (q.get("chop", 0) if CFG["use_chop"] else 0) + (q.get("dump", 0) if CFG["use_dump"] else 0))))


def classify(r):
    """한 주(row)의 지표 → 단계·라벨. r은 dict 또는 Series"""
    ma, close, slope, prior = r["ma30"], r["close"], r["slope"], r["prior"]
    if pd.isna(ma) or pd.isna(slope):
        return None
    prior = 0.0 if pd.isna(prior) else prior
    above = close > ma
    flat = CFG["flat_pct"]
    near_below = (not above) and close >= ma * 0.97
    bottom = ma < r["ma_peak"] * 0.85 or prior < -3
    if (r["crosses"] or 0) >= CFG["whipsaw"] and abs(slope) < 2 * flat:
        st = 1 if bottom else 3
    elif slope > flat and (above or near_below):
        st = 2
    elif slope < -flat and not above:
        st = 4
    else:
        # 평평한 30주선: 이전 고점보다 한참 아래(하락 후 바닥)면 1단계, 고점 근처면 3단계
        st = 1 if bottom else 3
    brk = not pd.isna(r["brk_week"])
    ext = r["ext"]
    label = {1: "1단계", 2: "2단계", 3: "3단계", 4: "4단계"}[st]
    if st == 1 and brk and above and slope >= -flat:
        st, label = 2, "1단계→2단계 돌파"
    elif st == 1 and close >= r["res"] * 0.95:
        label = "1단계 후반 (저항 근접)"
    elif st == 2 and ext >= CFG["overheat_pct"]:
        label = "2단계 (과열)"
    elif st == 2 and late_stage(r):
        label = "2단계 후반"
    elif st == 2 and not pd.isna(r.get("ma_up13", np.nan)) and r.get("ma_up13") < 1 and not brk:
        label = "2단계? (30주선 평평)"
    elif st == 2 and brk:
        label = "2단계 돌파"
    elif st == 2 and not above:
        label = "2단계 (30주선 시험)"
    elif st == 2 and r["weeks_above"] <= 12:
        label = "2단계 초기"
    elif st == 3 and above:
        label = "3단계 가능성 (30주선 평평)"
    return st, label


def row_dict(r):
    d = {k: (None if (not isinstance(v, (bool, np.bool_)) and pd.isna(v)) else (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, (bool, np.bool_)) else v))
         for k, v in r.items()}
    d["brk_week"] = int(d["brk_week"]) if d.get("brk_week") else None
    d["base_depth"] = (d["res"] - d["base_low"]) / d["res"] * 100 if d.get("res") and d.get("base_low") else 0
    return d


def stage_of(df):
    """마지막 주의 와인스테인 단계(1~4)와 지표들"""
    f = indicators(df)
    if f["ma30"].dropna().shape[0] < 30:
        return None
    r = f.iloc[-1]
    cl = classify(r)
    if cl is None:
        return None
    out = row_dict(r)
    out["stage"], out["stage_label"] = cl
    out["ma_series"] = f["ma30"]
    return out


def mansfield_series(c, b):
    b = b.reindex(c.index, method="ffill")
    rs = c / b
    return (rs / rs.rolling(52).mean() - 1) * 100


def mansfield(c, b):
    j = pd.concat([c, b], axis=1, join="inner").dropna()
    if len(j) < 60:
        return None, None
    rs = j.iloc[:, 0] / j.iloc[:, 1]
    m = (rs / rs.rolling(52).mean() - 1) * 100
    return float(m.iloc[-1]), float(m.iloc[-5])


def grade_from(points, maxp):
    return int(min(9, max(1, 9 - round(points / maxp * 8))))


def score_stock(s, mkt_stage, sec_stage, sec_rs):
    # 시장 10
    p_mkt = {2: 10, 1: 6, 3: 3, 4: 0}.get(mkt_stage, 5)
    # 업종 25
    if sec_stage is None:
        p_sec = 0
    else:
        p_sec = {2: 15, 1: 10, 3: 4, 4: 0}[sec_stage]
        if sec_rs is not None and sec_stage in (1, 2):
            p_sec += 10 if sec_rs > 5 else 7 if sec_rs > 0 else 3 if sec_rs > -5 else 0
    # 종목 위치 25
    st = s["stage"]
    p_st = 14 if st == 2 else 9 if st == 1 else 2
    if s["brk_week"]:
        p_st += 5
    if s["close"] >= s["hi_all"] * 0.98:
        p_st += 4                       # 신고가권: 위쪽 매물 없음
    elif s["hi_all"] <= s["close"] * 1.10:
        p_st -= 2                       # 10% 위에 과거 고점
    if s["ext"] > CFG["overheat_pct"]:
        p_st -= 6
    elif s["ext"] <= 10 and st == 2:
        p_st += 2
    if st == 1 and s["close"] >= s["res"] * 0.95:
        p_st += 3                       # 저항 근접 베이스
    p_st = max(0, min(25, p_st))
    # 거래량 15
    vr = s["brk_vr"] if s["brk_week"] else s["vr"]
    p_v = 10 if vr >= 3 else 8 if vr >= 2 else 5 if vr >= 1.5 else 3 if vr >= 1 else 1
    p_v += 5 if s["updn"] >= 1.5 else 3 if s["updn"] >= 1.1 else 1 if s["updn"] >= 0.9 else 0
    p_v = min(15, p_v)
    # RS 15
    rs, rs4 = s["rs"], s["rs4"]
    if rs is not None and rs4 is None:
        rs4 = rs
    if rs is None:
        p_rs = 5
    elif rs > 0 and rs4 is not None and rs4 <= 0:
        p_rs = 13                       # 0선 상향 돌파
    elif rs > 10 and rs >= rs4:
        p_rs = 15
    elif rs > 0 and rs >= rs4:
        p_rs = 12
    elif rs > 0:
        p_rs = 8
    elif rs4 is not None and rs > rs4:
        p_rs = 6
    else:
        p_rs = 2
    # 손절/위험 10
    sp = abs(s["stop_pct"])
    p_risk = 10 if sp <= 8 else 8 if sp <= 12 else 6 if sp <= 15 else 3 if sp <= 20 else 1
    raw = p_mkt + p_sec + p_st + p_v + p_rs + p_risk
    total, caps = raw, []
    if st in (3, 4):
        total = min(total, 30); caps.append(f"종목 {st}단계 상한 30")
    if sec_stage == 4:
        total = min(total, 40); caps.append("업종 4단계 상한 40")
    if mkt_stage == 4:
        total = min(total, 50); caps.append("시장 4단계 상한 50")
    elif mkt_stage == 3:
        total -= 10; caps.append("시장 3단계 -10")
    bd = dict(market=p_mkt, sector=p_sec, stock=p_st, volume=p_v, rs=p_rs, risk=p_risk)
    return int(max(0, total)), bd, caps


# ---------------- 점수표 v2: 손절폭이 작은 자리(돌파 직후·30주선 근처)에 높은 점수 ----------------
def signal_v2(s):
    st, c, ext = s["stage"], s["close"], s["ext"]
    bl = s.get("brk_level")
    if st == 2 and s["brk_week"] and (s["brk_vr"] or 0) >= 1.5 and bl and c <= bl * (1 + CFG["breakout_chase_pct"] / 100) and ext <= 15:
        return "돌파"
    if st == 2 and s["slope"] > 0 and (-3 <= ext <= 5 or (bl and abs(c / bl - 1) <= 0.03 and ext <= 12)):
        return "풀백"
    if st == 1 and c >= s["res"] * 0.95 and s["rs"] is not None and s["rs4"] is not None and s["rs"] > s["rs4"]:
        return "베이스 대기"
    return ""


def entry_stop(s, signal, entry=None):
    """매수 방식별 초기 손절가. 돌파=매수가 -3%, 풀백=최근 3주 저점·30주선 아래, 그 외=최근 10주 저점 아래"""
    e = entry if entry is not None else s["close"]
    if signal == "돌파":
        stop = e * (1 - CFG["breakout_stop_pct"] / 100)
    elif signal == "풀백":
        cands = [x for x in (s.get("low3"), s.get("ma30")) if x]
        stop = min(cands[0] * 0.99, cands[1] * 0.98) if len(cands) == 2 else (cands[0] * 0.99 if cands else s["stop"])
    else:
        stop = s["stop"]
    return min(stop, e * (1 - CFG["min_stop_pct"] / 100)) if stop else stop


def score_v2(s, mkt_stage, sec_stage, sec_rs, signal=None):
    """시장 10 · 업종 20 · 진입 위치 25 · 거래량 25 · RS 10 · 손절폭 10, 이격 과열은 추가 감점"""
    signal = signal_v2(s) if signal is None else signal
    st, ext, c = s["stage"], s["ext"], s["close"]
    p_mkt = {2: 10, 1: 6, 3: 3, 4: 0}.get(mkt_stage, 5)
    if sec_stage is None:
        p_sec = 0
    else:
        p_sec = {2: 12, 1: 8, 3: 3, 4: 0}[sec_stage]
        if sec_rs is not None and sec_stage in (1, 2):
            p_sec += 8 if sec_rs > 5 else 6 if sec_rs > 0 else 2 if sec_rs > -5 else 0
    # 진입 위치 25: 1→2단계 돌파 직후, 30주선·돌파 지점 근처가 최고점
    if signal == "돌파":
        p_pos = 25 if s["stage_label"].startswith("1단계→2단계") else 22
    elif signal == "풀백":
        p_pos = 23 if -3 <= ext <= 5 else 21
    elif st == 1 and c >= s["res"] * 0.95:
        p_pos = 18                                   # 돌파 직전 베이스
    elif st == 2:
        p_pos = 14 if ext <= 10 else 8 if ext <= 15 else 3 if ext <= 20 else 0
    elif st == 1:
        p_pos = 6
    else:
        p_pos = 0
    if c >= s["hi_all"] * 0.98:
        p_pos = min(25, p_pos + 2)                   # 신고가권: 위쪽 매물 없음
    # 거래량 25: 돌파 주 거래량 + 상승 주/하락 주 거래량 비
    vr = s["brk_vr"] if s["brk_week"] else s["vr"]
    p_v = 15 if vr >= 3 else 12 if vr >= 2 else 8 if vr >= 1.5 else 4 if vr >= 1 else 1
    if signal == "풀백" and (s["vr"] or 9) < 0.8:
        p_v = max(p_v, 8)                            # 거래량이 마르며 되돌리면 건전
    u = s["updn"]
    p_v += 10 if u >= 1.5 else 7 if u >= 1.2 else 4 if u >= 1.0 else 0
    p_v = min(25, p_v)
    # RS 10
    rs, rs4 = s["rs"], s["rs4"]
    if rs is not None and rs4 is None:
        rs4 = rs
    if rs is None:
        p_rs = 3
    elif rs > 0 and rs4 <= 0:
        p_rs = 9
    elif rs > 10 and rs >= rs4:
        p_rs = 10
    elif rs > 0 and rs >= rs4:
        p_rs = 8
    elif rs > 0:
        p_rs = 5
    elif rs > rs4:
        p_rs = 4
    else:
        p_rs = 1
    # 손절폭 10 (이 자리에서 사면 손절까지 몇 %인가)
    stop = entry_stop(s, signal)
    sp = abs(stop / c - 1) * 100 if stop else 99
    p_risk = 10 if sp <= 5 else 8 if sp <= 8 else 5 if sp <= 12 else 2 if sp <= 15 else 0
    raw = p_mkt + p_sec + p_pos + p_v + p_rs + p_risk
    total, caps = raw, []
    if ext > 30:
        total -= 15; caps.append("이격 30%+ -15")
    elif ext > 20:
        total -= 10; caps.append("이격 20%+ -10")
    if st in (3, 4):
        total = min(total, 30); caps.append(f"종목 {st}단계 상한 30")
    if sec_stage == 4:
        total = min(total, 40); caps.append("업종 4단계 상한 40")
    if mkt_stage == 4:
        total = min(total, 50); caps.append("시장 4단계 상한 50")
    elif mkt_stage == 3:
        total -= 10; caps.append("시장 3단계 -10")
    bd = dict(market=p_mkt, sector=p_sec, stock=p_pos, volume=p_v, rs=p_rs, risk=p_risk)
    return int(max(0, total)), bd, caps, stop


def signal_of(s):
    st, c = s["stage"], s["close"]
    if st == 2 and s["brk_week"] and (s["brk_vr"] or 0) >= 1.5 and s["ext"] < CFG["overheat_pct"]:
        return "돌파"
    if st == 2 and s["slope"] > 0 and (-3 <= s["ext"] <= 5 or (s["brk_level"] and abs(c / s["brk_level"] - 1) <= 0.03)):
        return "풀백"
    if st == 1 and c >= s["res"] * 0.95 and s["rs"] is not None and s["rs4"] is not None and s["rs"] > s["rs4"]:
        return "베이스 대기"
    return ""


# ---------------- 리포트 ----------------
def spark(df, ma, w=150, h=40, n=60):
    c = df["Close"].iloc[-n:]
    m = ma.iloc[-n:]
    lo, hi = float(min(c.min(), m.min())), float(max(c.max(), m.max()))
    if hi <= lo:
        return ""
    def pts(s):
        xs = np.linspace(2, w - 2, len(s))
        return " ".join(f"{x:.1f},{h - 2 - (float(y) - lo) / (hi - lo) * (h - 4):.1f}"
                        for x, y in zip(xs, s) if not math.isnan(y))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline points="{pts(m)}" fill="none" stroke="#8b5cf6" stroke-width="1.5"/>'
            f'<polyline points="{pts(c)}" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>')


def fmt(x, d=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:,.{d}f}"


def write_report(day, mk, sectors, rows, dfs):
    os.makedirs(OUT, exist_ok=True)
    e = html.escape
    def stage_pill(n, label=None):
        return f'<span class="p s{n}">{e(label or f"{n}단계")}</span>'
    css = """
    :root{--bg:#f4f6f5;--card:#fff;--fg:#17201c;--mut:#5b6862;--line:#d8dfdb;--g:#1d7a4b;--a:#b0741a;--r:#b13a3a;--b:#4f6d84}
    @media(prefers-color-scheme:dark){:root{--bg:#111614;--card:#19211d;--fg:#e4ebe7;--mut:#93a19a;--line:#2a3530;--g:#5fcf93;--a:#e5b45c;--r:#ef7f7f;--b:#8fb0c6}}
    body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;margin:0}
    .w{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
    h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
    .mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
    .tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
    table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
    th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
    th{font-size:12px;color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--card)}
    td.l,th.l{text-align:left}tr:last-child td{border-bottom:0}
    .p{display:inline-block;font-size:12px;font-weight:700;padding:1px 7px;border-radius:99px}
    .s1{color:var(--b);background:color-mix(in srgb,var(--b) 15%,transparent)}.s2{color:var(--g);background:color-mix(in srgb,var(--g) 15%,transparent)}
    .s3{color:var(--a);background:color-mix(in srgb,var(--a) 15%,transparent)}.s4{color:var(--r);background:color-mix(in srgb,var(--r) 15%,transparent)}
    .sc{font-weight:800;font-size:15px}.hi{color:var(--g)}.mid{color:var(--a)}.lo{color:var(--mut)}
    .warn{color:var(--r);font-weight:700}svg{color:var(--fg);vertical-align:middle}
    """
    def sc_cls(x):
        return "hi" if x >= 80 else "mid" if x >= 65 else "lo"
    parts = [f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>와인스테인 스크리너 {day}</title><style>{css}</style><div class='w'>",
             f"<h1>와인스테인 스크리너</h1><div class='mut'>기준: {day} 주봉 · {len(dfs)}종목 계산 · "
             f"보라선=30주선 · 점수는 기준 부합도이며 수익 보장이 아닙니다.</div>"]
    if dt.date.today().weekday() < 5:
        parts.append("<div class='card warn' style='margin-top:8px'>평일에 실행해서 이번 주 주봉이 아직 확정되지 않았습니다. "
                     "와인스테인 판단은 주봉 종가 기준이니 토요일(한국 시간)에 다시 돌려 확인하세요.</div>")
    mtxt = " · ".join(f"{MARKET[k]} {stage_pill(v['stage'], v['stage_label'])}" for k, v in mk.items() if v)
    warn = any(v and v["stage"] == 4 for v in mk.values())
    parts.append(f"<h2>시장</h2><div class='card'>{mtxt}"
                 f"{'<div class=warn>시장 4단계: 와인스테인 원칙상 신규 매수 보류</div>' if warn else ''}</div>")
    # 업종
    parts.append("<h2>업종 단계 (업종이 종목만큼 중요합니다)</h2><div class='tw'><table><tr>"
                 "<th class=l>업종</th><th class=l>ETF</th><th class=l>단계</th><th>30주선 대비</th><th>30주선 10주 기울기</th><th>RS</th><th class=l>최근 60주</th></tr>")
    order = {2: 0, 1: 1, 3: 2, 4: 3}
    for t, v in sorted(sectors.items(), key=lambda kv: (order[kv[1]["stage"]], -(kv[1]["rs"] or -99))):
        parts.append(f"<tr><td class=l>{e(v['name'])}</td><td class=l>{t}</td><td class=l>{stage_pill(v['stage'], v['stage_label'])}</td>"
                     f"<td>{fmt(v['ext'],1)}%</td><td>{fmt(v['slope'],1)}%</td><td>{fmt(v['rs'],1)}</td><td class=l>{spark(dfs[t], v['ma_series'])}</td></tr>")
    parts.append("</table></div>")
    # 신호별 후보
    heads = ("<tr><th class=l>티커</th><th class=l>종목</th><th class=l>업종</th><th>점수</th><th class=l>단계</th><th>종가</th>"
             "<th>매수 스톱</th><th>풀백 구간</th><th>손절</th><th>손절폭</th><th>30주선 대비</th><th>거래량</th><th>RS</th><th class=l>최근 60주</th></tr>")
    for sig, desc in [("돌파", "최근 3주 안에 26주 고점을 거래량과 함께 종가 돌파"),
                      ("풀백", "2단계 중 30주선 근처(-3~+5%) 또는 돌파 지점(±3%)으로 되돌림"),
                      ("베이스 대기", "1단계 베이스 상단 5% 이내, RS 개선 중")]:
        rs_ = [r for r in rows if r["signal"] == sig]
        parts.append(f"<h2>{sig} 후보 {len(rs_)}종목</h2><div class='mut' style='margin-bottom:6px'>{desc} · 점수 순</div>")
        if not rs_:
            parts.append("<div class='card mut'>해당 종목 없음</div>")
            continue
        parts.append(f"<div class='tw'><table>{heads}")
        for r in rs_[:60]:
            pz = f"{fmt(r['ma30'])}~{fmt(max(r['ma30']*1.05, r['brk_level'] or 0))}" if r["stage"] == 2 else "–"
            parts.append(
                f"<tr><td class=l><b>{e(r['ticker'])}</b></td><td class=l>{e(str(r['name'])[:24])}</td><td class=l>{e(r['sector_ko'] or '–')}</td>"
                f"<td class='sc {sc_cls(r['score'])}'>{r['score']}</td><td class=l>{stage_pill(r['stage'], r['stage_label'])}</td>"
                f"<td>{fmt(r['close'])}</td><td>{fmt(r['buy_stop']) if r['buy_stop'] and r['close'] < r['buy_stop'] else '돌파함'}</td><td>{pz}</td>"
                f"<td>{fmt(r['stop'])}</td><td class='{'warn' if abs(r['stop_pct'])>CFG['stop_risk_pct'] else ''}'>{fmt(r['stop_pct'],1)}%</td>"
                f"<td class='{'warn' if r['ext']>=CFG['overheat_pct'] else ''}'>{fmt(r['ext'],1)}%</td>"
                f"<td>{r['volGrade']}등급</td><td>{r['rsGrade']}등급</td><td class=l>{spark(dfs[r['ticker']], r['ma_series'])}</td></tr>")
        parts.append("</table></div>")
    parts.append("<p class='mut' style='margin-top:24px'>자동 계산은 차트 판독 대신 규칙을 기계적으로 적용한 것입니다. "
                 "매수 전에는 후보 종목의 주봉·일봉 차트를 프로젝트에 보내 최종 확인하세요.</p></div>")
    path = os.path.join(OUT, f"report_{day}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return path


# ---------------- 메인 ----------------
def compute(quick=False, fetch=fetch_weekly, universe=None, extra=()):
    """데이터 받기 + 시장·업종·종목 계산 (리포트·앱 공통)"""
    day = dt.date.today().isoformat()
    uni = universe or load_universe(quick)
    etfs = list(SECTOR_ETF.values()) + list(INDUSTRY_ETF)
    need = list(uni) + etfs + list(MARKET) + list(BENCH.values()) + list(extra)
    print(f"총 {len(need)}개 주봉 데이터를 받습니다 (몇 분 걸릴 수 있어요)")
    dfs = fetch(need)
    bench = {k: dfs[v]["Close"] for k, v in BENCH.items() if v in dfs}
    # 시장
    mk = {}
    for t in MARKET:
        mk[t] = stage_of(dfs[t]) if t in dfs else None
    mkt_stage = (mk.get("^GSPC") or mk.get("^IXIC") or {}).get("stage")
    worst = max([v["stage"] for v in mk.values() if v] or [2], key=lambda s: {4: 3, 3: 2, 1: 1, 2: 0}[s])
    # 업종 ETF
    sectors = {}
    names = {**{v: SECTOR_KO[k] + " (섹터)" for k, v in SECTOR_ETF.items()}, **INDUSTRY_ETF}
    for t in etfs:
        if t in dfs:
            s = stage_of(dfs[t])
            if s:
                s["rs"], s["rs4"] = mansfield(dfs[t]["Close"], bench["us"]) if "us" in bench else (None, None)
                s["name"] = names[t]
                sectors[t] = s
    rows = []
    for t, info in uni.items():
        df = dfs.get(t)
        if df is None:
            continue
        if not info.get("mine") and info["bench"] == "us":
            if df["Close"].iloc[-1] < CFG["min_price"]:
                continue
            if (df["Close"] * df["Volume"]).iloc[-10:].mean() < CFG["min_dollar_vol"]:
                continue
        s = stage_of(df)
        if not s:
            continue
        b = bench.get(info["bench"])
        s["rs"], s["rs4"] = mansfield(df["Close"], b) if b is not None else (None, None)
        etf = MYSECTOR_ETF.get(info.get("mysector") or "")
        if not etf or etf not in sectors:
            etf = SECTOR_ETF.get(info.get("sector") or "")
        sec = sectors.get(etf) if etf else None
        m_stage = worst if info["bench"] == "us" else None
        sig2 = signal_v2(s)
        score, bd, caps, stop2 = score_v2(s, m_stage, sec["stage"] if sec else None, sec["rs"] if sec else None, sig2)
        if sec is None and info["bench"] == "us":
            score = min(100, score + CFG["no_sector_pts"])
            caps = caps + [f"업종 정보 없음 · 중립 {CFG['no_sector_pts']}점"]
        q = quality_adj(s, sig2)
        score = apply_quality(score, q, CFG["use_late"], CFG["use_base"])
        caps = caps + [n for n in q["notes"] if q["pinned"] or (CFG["use_late"] and "후반" in n) or (CFG["use_base"] and "기저" in n)
                       or "30주선" in n or "헤드앤숄더" in n or "매물" in n or "재돌파" in n or "들락날락" in n or "급락" in n or "볼린저" in n]
        if q["pinned"]:
            sig2 = ""
        kind = signal_kind(s, sig2)
        if kind == "1→2 돌파" and CFG["b12_pts"] and not q["pinned"]:
            score = min(100, score + CFG["b12_pts"])          # 백테스트: 1→2 돌파 1.61R vs 2단계 중간 돌파 1.37R, 포트폴리오도 가장 좋았음
            caps = caps + [f"1→2단계 돌파 +{CFG['b12_pts']}"]
        s["kind"] = kind
        s["stop"], s["stop_pct"] = stop2, (stop2 / s["close"] - 1) * 100
        s.update(ticker=t, name=info["name"], score=score, breakdown=bd, caps=caps, cap=info.get("cap"), mine=bool(info.get("mine")),
                 sector_ko=info.get("mysector") or SECTOR_KO.get(info.get("sector") or "", None),
                 sector_etf=etf, sector_stage=sec["stage"] if sec else None,
                 volGrade=grade_from(bd["volume"], 25), rsGrade=grade_from(bd["rs"], 10),
                 buy_stop=round(s["res"] * 1.005, 2) if s["close"] <= s["res"]
                 else round(s["brk_level"] * 1.005, 2) if s["brk_level"] else None)
        s["signal"] = sig2
        rows.append(s)
    rows.sort(key=lambda r: -r["score"])
    return dict(day=day, mk=mk, sectors=sectors, rows=rows, dfs=dfs, uni=uni, bench=bench, worst=worst)


def run(quick=False, fetch=fetch_weekly, universe=None):
    R = compute(quick, fetch, universe)
    day, mk, sectors, rows, dfs = R["day"], R["mk"], R["sectors"], R["rows"], R["dfs"]
    rep = write_report(day, mk, sectors, rows, dfs)
    # CSV
    keep = ["ticker", "name", "sector_ko", "sector_etf", "sector_stage", "score", "signal", "stage", "stage_label",
            "close", "ma30", "slope", "ext", "res", "buy_stop", "stop", "stop_pct", "vr", "updn", "rs", "rs4",
            "volGrade", "rsGrade"]
    csv = os.path.join(OUT, f"results_{day}.csv")
    pd.DataFrame([{k: r.get(k) for k in keep} for r in rows]).to_csv(csv, index=False, encoding="utf-8-sig")
    # 보드 가져오기용 JSON (신호 종목 + 65점 이상)
    board = []
    for r in rows:
        if not r["signal"] and r["score"] < 65:
            continue
        board.append(dict(
            ticker=r["ticker"], name=str(r["name"]), sector=r["sector_ko"] or "업종 미지정", date=day,
            stage=r["stage"], stageLabel=r["stage_label"], score=r["score"], price=round(r["close"], 2), priceDate=day,
            buyStop=r["buy_stop"], pullbackZone=(f"{r['ma30']:.2f}~{max(r['ma30']*1.05, r['brk_level'] or 0):.2f}" if r["stage"] == 2 else None),
            stop=round(r["stop"], 2), extension=f"{r['ext']:+.0f}%", buyType=r["signal"] or "관찰",
            volGrade=r["volGrade"], rsGrade=r["rsGrade"], breakdown=r["breakdown"],
            capNote=", ".join(r["caps"]) or None, source="screener"))
    js = os.path.join(OUT, f"board_import_{day}.json")
    with open(js, "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False, indent=1)
    n = {k: sum(1 for r in rows if r["signal"] == k) for k in ("돌파", "풀백", "베이스 대기")}
    print(f"\n완료: {len(rows)}종목 계산 · 돌파 {n['돌파']} · 풀백 {n['풀백']} · 베이스 대기 {n['베이스 대기']}")
    print("리포트:", rep)
    return rep, rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="나스닥100 + 내 종목만")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    rep, _ = run(quick=a.quick)
    if not a.no_open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(rep))

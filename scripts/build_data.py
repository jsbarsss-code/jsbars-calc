"""
계산기용 정적 데이터 생성 — GitHub Actions가 주 1회 실행한다(서버 없음).

  site/data/apt-index.json      수도권 아파트 단지 검색 목록 [id, 단지명, 법정동, 시군구명, 시군구코드, 준공년]
  site/data/apt/{시군구코드}.json  단지별 전용면적별 최근 12개월 실거래 요약
  site/data/presale.json        청약홈 수도권 APT 분양 공고(최근 3년 — 입주 예정 단지 포함) + 주택형별 분양가
  site/data/meta.json           생성 시각·출처·건수

인증키: 환경변수 DATA_GO_KR_KEY (GitHub Secret). 로컬 시험은 --config 로 블로그 config/public-data-api.json 경로를 준다.
표준 라이브러리만 쓴다. 모든 반복은 상한이 있다(월 수·페이지·재시도·동시 실행 수).

사용법:
  python scripts/build_data.py --out site/data [--months 12] [--sgg 41480,11680] [--config path]
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APT_TRADE = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
APPLYHOME = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1"
MAX_PAGES = 10          # 시군구·월당 페이지 상한(1,000행 × 10)
RETRIES = 4             # 호출당 재시도 상한(429는 더 길게 쉰다)
WORKERS = 2             # 동시 호출 상한 — 6이면 429(속도 제한)에 걸린다
PRESALE_PAGES = 10      # 분양 공고 목록 페이지 상한(500행 × 10)

# 수도권 시군구(법정동 앞 5자리) — 블로그 scripts/public_data_fetch.py LAWD 표와 같다(2026-10 실호출 검증).
LAWD = {
    "11110": "서울 종로구", "11140": "서울 중구", "11170": "서울 용산구", "11200": "서울 성동구", "11215": "서울 광진구",
    "11230": "서울 동대문구", "11260": "서울 중랑구", "11290": "서울 성북구", "11305": "서울 강북구", "11320": "서울 도봉구",
    "11350": "서울 노원구", "11380": "서울 은평구", "11410": "서울 서대문구", "11440": "서울 마포구", "11470": "서울 양천구",
    "11500": "서울 강서구", "11530": "서울 구로구", "11545": "서울 금천구", "11560": "서울 영등포구", "11590": "서울 동작구",
    "11620": "서울 관악구", "11650": "서울 서초구", "11680": "서울 강남구", "11710": "서울 송파구", "11740": "서울 강동구",
    "41111": "수원 장안구", "41113": "수원 권선구", "41115": "수원 팔달구", "41117": "수원 영통구",
    "41131": "성남 수정구", "41133": "성남 중원구", "41135": "성남 분당구", "41150": "의정부시",
    "41171": "안양 만안구", "41173": "안양 동안구", "41192": "부천 원미구", "41194": "부천 소사구", "41196": "부천 오정구",
    "41210": "광명시", "41220": "평택시", "41271": "안산 상록구", "41273": "안산 단원구", "41281": "고양 덕양구",
    "41285": "고양 일산동구", "41287": "고양 일산서구", "41290": "과천시", "41310": "구리시",
    "41360": "남양주시", "41370": "오산시", "41390": "시흥시", "41410": "군포시", "41430": "의왕시",
    "41450": "하남시", "41461": "용인 처인구", "41463": "용인 기흥구", "41465": "용인 수지구",
    "41480": "파주시", "41500": "이천시", "41570": "김포시", "41591": "화성 만세구", "41593": "화성 효행구",
    "41595": "화성 병점구", "41597": "화성 동탄구", "41610": "광주시", "41630": "양주시",
    "28125": "인천 제물포구", "28155": "인천 영종구", "28177": "인천 미추홀구", "28185": "인천 연수구",
    "28200": "인천 남동구", "28237": "인천 부평구", "28245": "인천 계양구", "28275": "인천 서해구", "28290": "인천 검단구",
}
PRESALE_AREAS = ("서울", "경기", "인천")


def http(url: str) -> bytes:
    last = None
    for attempt in range(RETRIES + 1):
        try:
            return urllib.request.urlopen(url, timeout=30).read()
        except Exception as e:  # 네트워크·일시 오류·429만 재시도
            last = e
            wait = 6 * (attempt + 1) if "429" in str(e) else 1.5 * (attempt + 1)
            time.sleep(wait)
    raise RuntimeError(f"호출 실패({RETRIES + 1}회): {last}")


def months_back(n: int) -> list:
    d = date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def trade_month(key: str, code: str, ym: str) -> list:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        q = urllib.parse.urlencode({"LAWD_CD": code, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000})
        root = ET.fromstring(http(f"{APT_TRADE}?serviceKey={key}&{q}"))
        rc = root.findtext("header/resultCode")
        if rc not in ("000", "00"):
            raise RuntimeError(f"{code} {ym} resultCode={rc} {root.findtext('header/resultMsg')}")
        items = root.findall("body/items/item")
        rows += [{e.tag: (e.text or "").strip() for e in it} for it in items]
        if not items or len(rows) >= int(root.findtext("body/totalCount") or 0):
            break
    return rows


def build_trades(key: str, codes: list, months: int, out: Path) -> tuple:
    jobs = [(c, ym) for c in codes for ym in months_back(months)]
    errors, by_sgg = [], {c: [] for c in codes}

    def run(job):
        c, ym = job
        try:
            return c, trade_month(key, c, ym), None
        except Exception as e:
            return c, [], f"{c} {ym}: {e}"

    failed = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for job, (c, rows, err) in zip(jobs, ex.map(run, jobs)):
            by_sgg[c] += rows
            if err:
                failed.append(job)
    # 실패분만 1회 더, 순차로 천천히(두 번째 패스가 마지막 — 그래도 실패하면 오류로 남긴다)
    for job in failed:
        time.sleep(1)
        c, rows, err = run(job)
        by_sgg[c] += rows
        if err:
            errors.append(err)

    index, n_trades = [], 0
    (out / "apt").mkdir(parents=True, exist_ok=True)
    for code in codes:
        complexes = {}
        for r in by_sgg[code]:
            if r.get("cdealType") == "O":          # 해제된 거래 제외
                continue
            try:
                price = int(r["dealAmount"].replace(",", ""))
                area = round(float(r["excluUseAr"]))
                day = f"{r['dealYear']}-{int(r['dealMonth']):02d}-{int(r['dealDay']):02d}"
            except (KeyError, ValueError):
                continue
            k = (r.get("umdNm", ""), r.get("jibun", ""), r.get("aptNm", ""))
            cx = complexes.setdefault(k, {"y": r.get("buildYear", ""), "a": {}})
            cx["a"].setdefault(area, []).append((day, price, r.get("floor", "")))
            n_trades += 1
        sgg_out = {}
        for (umd, jibun, name), cx in sorted(complexes.items()):
            cid = f"{code}-{len(sgg_out)}"
            areas = []
            for area, deals in sorted(cx["a"].items()):
                deals.sort(reverse=True)
                last = deals[0]
                recent = sorted(p for _, p, _ in deals[:5])
                areas.append([area, last[1], last[0], last[2], len(deals), recent[len(recent) // 2], max(p for _, p, _ in deals)])
            sgg_out[cid] = {"n": name, "u": umd, "j": jibun, "y": cx["y"], "a": areas}
            index.append([cid, name, umd, LAWD[code], code, cx["y"]])
        (out / "apt" / f"{code}.json").write_text(json.dumps(sgg_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "apt-index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(index), n_trades, errors


def odcloud(key: str, op: str, cond: dict, per: int, pages: int) -> list:
    rows, k = [], urllib.parse.quote(key)
    for page in range(1, pages + 1):
        q = urllib.parse.urlencode({"page": page, "perPage": per, **{f"cond[{a}]": b for a, b in cond.items()}})
        d = json.loads(http(f"{APPLYHOME}/{op}?{q}&serviceKey={k}"))
        rows += d.get("data", [])
        if not d.get("data") or len(rows) >= d.get("matchCount", 0):
            break
    return rows


def build_presale(key: str, out: Path) -> tuple:
    since = (date.today() - timedelta(days=1100)).isoformat()  # 공고~입주 약 3년 — 지금 입주하는 단지까지
    rows = odcloud(key, "getAPTLttotPblancDetail", {"RCRIT_PBLANC_DE::GTE": since}, 500, PRESALE_PAGES)
    rows = [r for r in rows if r.get("SUBSCRPT_AREA_CODE_NM") in PRESALE_AREAS]
    errors = []

    def types(r):
        try:
            return odcloud(key, "getAPTLttotPblancMdl", {"HOUSE_MANAGE_NO::EQ": r["HOUSE_MANAGE_NO"]}, 100, 1), None
        except Exception as e:
            return [], f"{r.get('HOUSE_MANAGE_NO')}: {e}"

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        mdls = list(ex.map(types, rows))
    items = []
    for r, (mdl, err) in zip(rows, mdls):
        if err:
            errors.append(err)
        ty = []
        for m in mdl:
            try:
                amt = int(re.sub(r"\D", "", str(m.get("LTTOT_TOP_AMOUNT") or "0")) or 0)
                excl = round(float(re.match(r"[\d.]+", str(m.get("HOUSE_TY", "0"))).group(0)))
            except (ValueError, AttributeError):
                continue
            if amt:
                ty.append([str(m.get("HOUSE_TY", "")).strip().lstrip("0"), excl, amt, int(m.get("SUPLY_HSHLDCO") or 0)])
        items.append({
            "no": r.get("HOUSE_MANAGE_NO"), "n": (r.get("HOUSE_NM") or "").strip(), "addr": (r.get("HSSPLY_ADRES") or "").strip(),
            "area": r.get("SUBSCRPT_AREA_CODE_NM"), "notice": r.get("RCRIT_PBLANC_DE"),
            "rcpt": r.get("GNRL_RNK1_CRSPAREA_RCPTDE") or r.get("RCEPT_BGNDE"), "win": r.get("PRZWNER_PRESNATN_DE"),
            "cntrct": [r.get("CNTRCT_CNCLS_BGNDE"), r.get("CNTRCT_CNCLS_ENDDE")], "mvn": r.get("MVN_PREARNGE_YM"),
            "hh": r.get("TOT_SUPLY_HSHLDCO"), "spec": r.get("SPECLT_RDN_EARTH_AT") == "Y",
            "adj": r.get("MDAT_TRGET_AREA_SECD") == "Y", "cap": r.get("PARCPRC_ULS_AT") == "Y",
            "url": r.get("PBLANC_URL"), "t": ty,
        })
    items.sort(key=lambda x: x.get("notice") or "", reverse=True)
    (out / "presale.json").write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(items), errors


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--sgg", default="", help="시험용: 쉼표로 시군구 코드 일부만")
    ap.add_argument("--config", default="", help="로컬 시험용 config/public-data-api.json")
    ap.add_argument("--skip-presale", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("DATA_GO_KR_KEY", "")
    if not key and a.config:
        key = json.loads(Path(a.config).read_text(encoding="utf-8"))["serviceKey"]
    if not key:
        print("FAIL: DATA_GO_KR_KEY 없음")
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    codes = [c.strip() for c in a.sgg.split(",") if c.strip() in LAWD] or list(LAWD)
    months = max(1, min(a.months, 24))

    t0 = time.time()
    n_cx, n_tr, err_tr = build_trades(key, codes, months, out)
    n_ps, err_ps = (0, []) if a.skip_presale else build_presale(key, out)
    meta = {
        "built": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST"),
        "months": months, "sgg": len(codes), "complexes": n_cx, "trades": n_tr, "presale": n_ps,
        "errors": len(err_tr) + len(err_ps),
        "source": "국토교통부 아파트 매매 실거래가(공공데이터포털), 한국부동산원 청약홈 분양정보 조회 서비스",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    for e in (err_tr + err_ps)[:20]:
        print("ERR", e)
    print(f"done in {time.time() - t0:.0f}s")
    # 실패가 전체 호출의 10%를 넘으면 배포를 막는다(옛 데이터를 반쪽 데이터로 덮지 않게)
    total_calls = len(codes) * months
    return 1 if len(err_tr) > total_calls * 0.1 or (not a.skip_presale and n_ps == 0) else 0


if __name__ == "__main__":
    sys.exit(main())

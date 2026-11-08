"""검색 유입용 정적 페이지 생성 — 배포(GitHub Actions)가 data 브랜치를 받은 뒤 매번 실행한다.

입력: site/data (meta.json · apt-index.json · apt/{시군구}.json · presale.json)
출력: site/apt/ (지역·단지 페이지) · site/presale/ (분양 단지 페이지) · site/sitemap.xml

계산 공식은 index.html 계산기(evaluate)와 같다. 바꾸면 둘 다 바꾼다.
사용: python build_pages.py site
"""
import html
import json
import os
import re
import sys
from urllib.parse import quote, urlencode

BASE = "https://jsbarsss-code.github.io/jsbars-calc/"
MIN_TRADES = 5          # 최근 12개월 거래가 이만큼 있는 단지만 페이지를 만든다(얇은 페이지 방지)
RATE, YEARS = 4.2, 30   # 계산기 기본값과 같게
LTV = {"reg": (40, 70), "capital": (70, 80), "local": (70, 80)}   # (일반 무주택, 생애최초)
STRESS = {"reg": 3.0, "capital": 3.0, "local": 0.75}
REG_SGG = {"41290", "41210", "41131", "41133", "41135", "41111", "41115", "41117", "41173", "41465", "41430", "41450", "41597", "41463", "41310"}
REGION_NAME = {"reg": "규제지역", "capital": "수도권", "local": "지방"}


# ---------- 계산 (단위: 만원, index.html과 같은 공식) ----------
def annuity(rate, years):
    i, n = rate / 100 / 12, years * 12
    return 12 / n if i == 0 else 12 * i / (1 - (1 + i) ** -n)


def cap_limit(region, price):
    if region == "local":
        return float("inf")
    return 60000 if price <= 150000 else (40000 if price <= 250000 else 20000)


def acq_tax(price, first):
    eok = price / 10000
    r = 1 if eok <= 6 else (eok * 2 / 3 - 3 if eok <= 9 else 3)
    tax = price * r / 100
    relief = min(tax, 200) if first and eok <= 12 else 0
    return tax + tax * 0.1 - relief


def broker_fee(price):
    if price < 5000:
        return min(price * 0.006, 25)
    if price < 20000:
        return min(price * 0.005, 80)
    r = 0.4 if price < 90000 else 0.5 if price < 120000 else 0.6 if price < 150000 else 0.7
    return price * r / 100


def plan_for(price, region, first, fee=True):
    """대출을 최대로 받을 때: 대출액 · 필요한 현금 · 그 대출을 다 받는 데 필요한 연봉"""
    ltv = LTV[region][1 if first else 0]
    loan = min(price * ltv / 100, cap_limit(region, price), price)
    cash = price - loan + acq_tax(price, first) + (broker_fee(price) if fee else 0)
    salary = loan * annuity(RATE + STRESS[region], YEARS) / 0.4
    monthly = loan * annuity(RATE, YEARS) / 12
    return {"loan": loan, "cash": cash, "salary": -(-salary // 100) * 100, "monthly": monthly, "ltv": ltv}


def won(man):
    man = int(round(man))
    neg, man = man < 0, abs(man)
    eok, rest = divmod(man, 10000)
    s = (f"{eok}억 {rest:,}만원" if rest else f"{eok}억원") if eok else f"{rest:,}만원"
    return ("-" if neg else "") + s


def won_short(man):   # 표용: 3.2억 / 8,500만
    man = int(round(man))
    return f"{man / 10000:.1f}억".replace(".0억", "억") if man >= 10000 else f"{man:,}만"


def pyeong(excl, supply=0, ty=""):
    if supply and supply > 0:
        m = re.search(r"[A-Za-z]+\s*$", str(ty or ""))
        return f"{round(supply / 3.3058)}{m.group(0).strip().upper() if m else ''}평형"
    r = 1.40 if excl < 70 else (1.33 if excl <= 100 else 1.30)
    return f"약 {round(excl * r / 3.3058)}평형"


def e(s):
    return html.escape(str(s), quote=True)


def region_for_sgg(code):
    return "reg" if code.startswith("11") or code in REG_SGG else "capital"


# ---------- 공통 틀 ----------
CSS = """:root{--bg:#f3f5f2;--paper:#fff;--ink:#17251f;--muted:#4a5751;--line:#d6ddd8;--accent:#1f6b4f;--accent-soft:#e2efe8;--warn:#9a5b00;
--font:"Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#101814;--paper:#17221d;--ink:#eef4f0;--muted:#b3c2ba;--line:#2c3a33;--accent:#5fc39a;--accent-soft:#1d3a2e;--warn:#e2a64a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#101814;--paper:#17221d;--ink:#eef4f0;--muted:#b3c2ba;--line:#2c3a33;--accent:#5fc39a;--accent-soft:#1d3a2e;--warn:#e2a64a;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:17px;line-height:1.7;word-break:keep-all;overflow-wrap:anywhere}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px;display:grid;gap:18px}
a{color:var(--accent)}.crumb{font-size:14px;color:var(--muted)}.crumb a{color:var(--muted)}
h1{font-size:clamp(24px,6.4vw,32px);line-height:1.35;margin:0;font-weight:800;letter-spacing:-.01em;text-wrap:balance}
h2{font-size:20px;margin:0;font-weight:800}.sub{margin:0;color:var(--muted)}
.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;gap:10px}
.trust{padding:10px 14px;border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:0 10px 10px 0;font-size:14px;color:var(--muted)}
.trust b{color:var(--ink)}.big{font-size:22px;font-weight:800}.muted{color:var(--muted);font-size:14px}
table{width:100%;border-collapse:collapse;font-size:15px}th,td{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13.5px}td.n,th.n{text-align:right;white-space:nowrap}
.cta{display:block;text-align:center;background:var(--accent);color:#fff;border-radius:10px;padding:14px;font-weight:800;text-decoration:none;font-size:17px}
:root[data-theme="dark"] .cta{color:#0d1a14}@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .cta{color:#0d1a14}}
.cta.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chips a{border:1px solid var(--line);background:var(--paper);border-radius:999px;padding:6px 12px;text-decoration:none;color:var(--ink);font-size:15px}
.list a{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);text-decoration:none;color:var(--ink)}
.list a span:last-child{color:var(--muted);white-space:nowrap;font-size:15px}.foot{font-size:13px;color:var(--muted);margin:0}
.key{display:inline-block;width:22px;height:22px;vertical-align:-4px;margin-right:6px}"""

KEY_SVG = ('<svg class="key" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
           '<circle cx="8" cy="8" r="5"/><path d="M11.5 11.5 21 21M17 17l2-2M14.5 14.5l2-2"/></svg>')


def page(path, title, desc, body, root, built):
    url = BASE + path
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="article">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{BASE}og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="{root}p.css">
<script data-goatcounter="https://jsbars.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>
</head>
<body>
<main>
<nav class="crumb"><a href="{root}">{KEY_SVG}우리집 열쇠가 내 손에</a></nav>
{body}
<p class="trust"><b>공공데이터 · 매주 자동 반영</b> · 마지막 갱신 {e(built)}<br>국토교통부 아파트 매매 실거래가(공공데이터포털), 한국부동산원 청약홈 분양정보. 신고가 늦은 거래는 빠질 수 있어요.</p>
<p class="foot">참고용 추정치예요. 금리 {RATE}% · {YEARS}년 원리금균등 · DSR 40% · 스트레스 금리(수도권·규제지역 3%p) · 기존 대출 없음 · 수도권 주담대 한도(15억 이하 6억 · 25억 이하 4억 · 초과 2억) 기준이며, 실제 한도와 금리는 은행 심사로 정해집니다. 규제지역 여부는 직접 확인해 주세요.</p>
<p class="foot">© 2026 jsbars · 우리집 열쇠 · <a href="{root}terms.html">이용약관·개인정보 안내</a> · <a href="{root}apt/">지역별 실거래</a> · <a href="{root}presale/">분양 단지</a></p>
</main>
</body>
</html>
"""


def calc_link(root, name, price, region, src, ps=None):
    q = {"name": name[:40], "price": int(round(price)), "region": region, "first": 1, "src": src}
    if ps:
        q["ps"] = ps
    return root + "?" + urlencode(q, quote_via=quote) + "#calc"


def plan_rows(price, region, fee=True):
    rows = []
    for first, label in ((True, "생애최초"), (False, "무주택 일반")):
        p = plan_for(price, region, first, fee)
        rows.append(f"<tr><td>{label}<br><span class='muted'>LTV {p['ltv']}%</span></td><td class='n'>{won_short(p['loan'])}</td>"
                    f"<td class='n'><b>{won_short(p['cash'])}</b></td><td class='n'>{won_short(p['salary'])}</td></tr>")
    return ("<table><thead><tr><th>구분</th><th class='n'>대출 최대</th><th class='n'>필요한 현금</th><th class='n'>필요 연봉</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


# ---------- 단지 · 지역 ----------
def build_apt(data, out, built, urls):
    idx = json.load(open(os.path.join(data, "apt-index.json"), encoding="utf-8"))
    by_sgg = {}
    for x in idx:
        by_sgg.setdefault(x[4], []).append(x)
    regions = []   # (sgg, 지역명, 페이지 수)
    n_pages = 0
    for sgg, xs in sorted(by_sgg.items()):
        f = os.path.join(data, "apt", sgg + ".json")
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        rname = xs[0][3]
        region = region_for_sgg(sgg)
        items = []
        for x in xs:
            cx = d.get(x[0])
            if not cx or not cx.get("a"):
                continue
            cnt = sum(a[4] for a in cx["a"])
            if cnt < MIN_TRADES:
                continue
            main_a = max(cx["a"], key=lambda a: a[4])
            items.append({"id": x[0], "num": x[0].split("-", 1)[1], "cx": cx, "cnt": cnt, "main": main_a})
        if not items:
            continue
        os.makedirs(os.path.join(out, "apt", sgg), exist_ok=True)
        by_dong = {}
        for it in items:
            by_dong.setdefault(it["cx"]["u"], []).append(it)
        items.sort(key=lambda it: -it["cnt"])
        for it in items:
            write_complex(out, sgg, rname, region, it, by_dong, items, built, urls)
            n_pages += 1
        write_region(out, sgg, rname, region, items, built, urls)
        regions.append((sgg, rname, len(items)))
    write_hub(out, regions, built, urls)
    return n_pages, len(regions)


def write_complex(out, sgg, rname, region, it, by_dong, items, built, urls):
    cx, root = it["cx"], "../../"
    path = f"apt/{sgg}/{it['num']}.html"
    name = cx["n"]
    m = it["main"]
    mp = m[5] or m[1]
    p0 = plan_for(mp, region, True)
    cards = []
    for a in sorted(cx["a"], key=lambda a: a[0]):
        price = a[5] or a[1]
        label = f"{pyeong(a[0])} · 전용 {a[0]}㎡"
        cards.append(f"""<section class="card">
<h2>{e(label)}</h2>
<p style="margin:0">최근 거래 <b>{won(a[1])}</b> <span class="muted">({e(a[2])} · {e(a[3])}층)</span><br>
<span class="muted">최근 5건 중간값 {won(a[5])} · 12개월 최고 {won(a[6])} · 최근 12개월 {a[4]}건</span></p>
<p class="muted" style="margin:0">중간값 {won(price)}에 대출을 최대로 받으면</p>
{plan_rows(price, region)}
<a class="cta ghost" href="{e(calc_link(root, f"{name} {pyeong(a[0]).replace('약 ', '')}({a[0]}㎡)", price, region, 'apt'))}">이 면적, 내 연봉·현금으로 계산하기</a>
</section>""")
    near = [x for x in by_dong.get(cx["u"], []) if x is not it][:8]
    if len(near) < 4:
        near += [x for x in items if x is not it and x not in near][: 8 - len(near)]
    chips = "".join(f'<a href="{e(x["num"])}.html">{e(x["cx"]["n"])}</a>' for x in near)
    title = f"{name} 실거래가로 계산한 필요 현금·연봉 ({rname} {cx['u']})"
    desc = (f"{rname} {cx['u']} {name} {pyeong(m[0])}(전용 {m[0]}㎡) 최근 거래 {won(m[1])}. "
            f"생애최초 대출 최대 {won(p0['loan'])}면 현금 {won(p0['cash'])}, 연봉 {won(p0['salary'])}이 필요해요. 면적별로 내 조건 계산.")
    body = f"""<nav class="crumb"><a href="{root}apt/">지역별 실거래</a> › <a href="../{sgg}.html">{e(rname)}</a></nav>
<h1>{e(name)}, 얼마 있어야 살 수 있을까?</h1>
<p class="sub">{e(rname)} {e(cx['u'])}{(' · ' + e(cx['y']) + '년 준공') if cx.get('y') else ''} · 최근 12개월 거래 {it['cnt']}건 · {REGION_NAME[region]} 기준</p>
<section class="card">
<p style="margin:0">가장 많이 거래된 <b>{e(pyeong(m[0]))}(전용 {m[0]}㎡)</b>, 중간값 {won(mp)} 기준</p>
<p class="big" style="margin:0">현금 {won(p0['cash'])} + 연봉 {won(p0['salary'])}</p>
<p class="muted" style="margin:0">생애최초로 대출 {won(p0['loan'])}(LTV {p0['ltv']}%)을 다 받을 때예요. 월 상환 약 {won(p0['monthly'])}. 취득세·중개보수 포함.</p>
<a class="cta" href="{e(calc_link(root, f"{name} {pyeong(m[0]).replace('약 ', '')}({m[0]}㎡)", mp, region, 'apt'))}">내 연봉·현금으로 다시 계산하기</a>
</section>
{''.join(cards)}
<section class="card"><h2>처음 집을 산다면</h2>
<p style="margin:0">계약 전 준비부터 잔금·입주까지 순서를 <a href="{root}">열쇠까지 지도</a>에서 한 단계씩 확인할 수 있어요. 계약했다면 <a href="{root}#plan">입주까지 일정</a>에서 잔금·이사 일정을 챙기세요.</p></section>
<section class="card"><h2>{e(cx['u'])} 근처 다른 단지</h2><div class="chips">{chips}</div></section>"""
    write(out, path, page(path, title, desc, body, root, built))
    urls.append(path)


def write_region(out, sgg, rname, region, items, built, urls):
    root, path = "../", f"apt/{sgg}.html"
    rows = []
    for it in items:
        m = it["main"]
        p = plan_for(m[5] or m[1], region, True)
        rows.append(f'<a href="{sgg}/{e(it["num"])}.html"><span>{e(it["cx"]["n"])} <span class="muted">{e(it["cx"]["u"])} · {e(pyeong(m[0]))}</span></span>'
                    f'<span>{won_short(m[5] or m[1])} · 현금 {won_short(p["cash"])}</span></a>')
    cheap = sorted(items, key=lambda it: it["main"][5] or it["main"][1])[:3]
    cheap_txt = ", ".join(f"{it['cx']['n']}({won_short(it['main'][5] or it['main'][1])})" for it in cheap)
    title = f"{rname} 아파트 실거래가로 보는 필요 현금·연봉 — 단지 {len(items)}곳"
    desc = f"{rname} 아파트 {len(items)}개 단지의 최근 실거래 중간값과 생애최초 대출 최대 시 필요한 현금. 가격이 낮은 단지: {cheap_txt}."
    body = f"""<nav class="crumb"><a href="{root}apt/">지역별 실거래</a></nav>
<h1>{e(rname)} 아파트, 얼마 있어야 살 수 있을까?</h1>
<p class="sub">최근 12개월 거래 {MIN_TRADES}건 이상 단지 {len(items)}곳 · 가장 많이 거래된 면적의 중간값 · {REGION_NAME[region]} 생애최초 기준 필요한 현금</p>
<a class="cta" href="{root}#calc">내 연봉·현금으로 닿는 집 찾기</a>
<section class="card list">{''.join(rows)}</section>"""
    write(out, path, page(path, title, desc, body, root, built))
    urls.append(path)


def write_hub(out, regions, built, urls):
    root, path = "../", "apt/index.html"
    groups = {}
    for sgg, rname, n in regions:
        groups.setdefault(rname.split(" ")[0], []).append((sgg, rname, n))
    secs = "".join(f"<section class='card'><h2>{e(g)}</h2><div class='chips'>"
                   + "".join(f'<a href="{sgg}.html">{e(rn.split(" ", 1)[-1])} <span class="muted">{n}</span></a>' for sgg, rn, n in xs)
                   + "</div></section>" for g, xs in groups.items())
    body = f"""<h1>지역별 아파트, 얼마 있어야 살 수 있을까?</h1>
<p class="sub">서울·경기·인천 {len(regions)}개 시군구 아파트 실거래가로 필요한 현금과 연봉을 계산해 두었어요. 지역을 고르세요.</p>
{secs}"""
    write(out, path, page("apt/", "서울·경기·인천 아파트 실거래가로 계산한 필요 현금·연봉", "시군구별 아파트 단지의 최근 실거래 중간값과 생애최초 대출 최대 시 필요한 현금·연봉. 매주 자동 갱신.", body, root, built))
    urls.append("apt/")


# ---------- 분양 ----------
def presale_region(p):
    return "reg" if (p.get("spec") or p.get("adj") or p.get("area") == "서울") else ("capital" if p.get("area") else "local")


def ty_name(t):
    return re.sub(r"^0*(\d+)\.\d+", r"\1", str(t or ""))


def ym(s):
    s = str(s or "")
    return f"{s[:4]}년 {int(s[4:6])}월" if re.fullmatch(r"\d{6}", s) else s


def build_presale(data, out, built, urls):
    ps = json.load(open(os.path.join(data, "presale.json"), encoding="utf-8"))
    ps = [p for p in ps if p.get("t")]
    os.makedirs(os.path.join(out, "presale"), exist_ok=True)
    for p in ps:
        write_presale(out, p, built, urls)
    root, path = "../", "presale/index.html"
    ps_sorted = sorted(ps, key=lambda p: p.get("rcpt") or p.get("notice") or "", reverse=True)
    rows = "".join(f'<a href="{e(p["no"])}.html"><span>{e(p["n"])} <span class="muted">{e(p.get("area", ""))}</span></span>'
                   f'<span>{e((p.get("rcpt") or "")[:7])} · 최고 {won_short(max(t[2] for t in p["t"]))}</span></a>' for p in ps_sorted)
    body = f"""<h1>분양 단지별 계약금·필요 현금·연봉</h1>
<p class="sub">청약홈 분양 공고 {len(ps)}건의 분양가로 계약금·중도금·잔금과 잔금 때 필요한 현금·연봉을 계산해 두었어요. 최근 접수순.</p>
<section class="card list">{rows}</section>"""
    write(out, path, page("presale/", "분양 단지별 계약금·중도금·잔금, 필요 현금·연봉 계산", "수도권 분양 공고 단지별 분양가로 계약금 10%·중도금 60%·잔금 30%와 잔금 때 필요한 현금·연봉을 계산. 매주 자동 갱신.", body, root, built))
    urls.append("presale/")
    return len(ps)


def write_presale(out, p, built, urls):
    root, path = "../", f"presale/{p['no']}.html"
    region = presale_region(p)
    types = sorted(p["t"], key=lambda t: (t[1], t[0]))
    pay_rows, cash_rows = [], []
    for t in types:
        price, lab = t[2], f"{ty_name(t[0])} · {pyeong(t[1], t[4] if len(t) > 4 else 0, t[0])}"
        pay_rows.append(f"<tr><td>{e(lab)}</td><td class='n'><b>{won_short(price)}</b></td><td class='n'>{won_short(price * .1)}</td>"
                        f"<td class='n'>{won_short(price * .6)}</td><td class='n'>{won_short(price * .3)}</td></tr>")
        q = plan_for(price, region, True, fee=False)
        cash_rows.append(f"<tr><td>{e(lab)}</td><td class='n'>{won_short(q['loan'])}</td><td class='n'><b>{won_short(q['cash'])}</b></td><td class='n'>{won_short(q['salary'])}</td></tr>")
    lo = min(types, key=lambda t: t[2])
    q0 = plan_for(lo[2], region, True, fee=False)
    flags = [x for x, on in (("분양가상한제", p.get("cap")), ("투기과열지구", p.get("spec")), ("조정대상지역", p.get("adj"))) if on]
    cn = p.get("cntrct") or []
    info = [("청약 접수", p.get("rcpt")), ("당첨 발표", p.get("win")), ("계약", " ~ ".join(cn) if cn else ""), ("입주 예정", ym(p.get("mvn"))), ("공급 세대", f"{p['hh']}세대" if p.get("hh") else "")]
    info_html = "".join(f"<tr><th>{k}</th><td>{e(v)}</td></tr>" for k, v in info if v)
    name = p["n"]
    short = re.sub(r"\s*(입주자모집공고|추가입주자모집공고)$", "", name)
    title = f"{short} 분양가로 계산한 계약금·필요 현금·연봉"
    desc = (f"{short} 분양가 {won(lo[2])}~{won(max(t[2] for t in types))}. 계약금 10%는 {won(lo[2] * .1)}부터, "
            f"잔금 때 생애최초 대출 최대면 현금 {won(q0['cash'])}·연봉 {won(q0['salary'])}이 필요해요. 청약 접수 {p.get('rcpt') or '-'}.")
    plan_q = urlencode({"to": "plan", "name": short[:40], "price": int(lo[2]), "ps": p["no"]}, quote_via=quote)
    body = f"""<nav class="crumb"><a href="{root}presale/">분양 단지</a></nav>
<h1>{e(short)}, 얼마 있어야 들어갈 수 있을까?</h1>
<p class="sub">{e(p.get('addr', ''))}{(' · ' + ' · '.join(flags)) if flags else ''}</p>
<section class="card">
<p style="margin:0">가장 낮은 분양가 <b>{e(ty_name(lo[0]))}타입 {won(lo[2])}</b> 기준</p>
<p class="big" style="margin:0">계약 때 {won(lo[2] * .1)} · 잔금 때 현금 {won(q0['cash'])}</p>
<p class="muted" style="margin:0">잔금 때 생애최초로 대출 {won(q0['loan'])}(LTV {q0['ltv']}%)을 다 받으려면 연봉 {won(q0['salary'])}이 필요해요. 취득세 포함, 중도금은 중도금 대출로 낸다고 가정.</p>
<a class="cta" href="{e(calc_link(root, short + ' ' + ty_name(lo[0]), lo[2], region, 'presale', p['no']))}">내 연봉·현금으로 다시 계산하기</a>
</section>
<section class="card"><h2>일정</h2><table><tbody>{info_html}</tbody></table>
<a class="cta ghost" href="{e(root + '?' + plan_q)}">계약금·중도금·잔금 날짜를 일정으로 만들기</a></section>
<section class="card"><h2>타입별 계약금·중도금·잔금</h2>
<p class="muted" style="margin:0">주택형별 최고가에 보통 비율(계약금 10% · 중도금 60% · 잔금 30%)을 적용한 추정이에요. 실제 비율·회차는 공고문을 따르세요.</p>
<table><thead><tr><th>타입</th><th class='n'>분양가</th><th class='n'>계약금</th><th class='n'>중도금</th><th class='n'>잔금</th></tr></thead><tbody>{''.join(pay_rows)}</tbody></table></section>
<section class="card"><h2>잔금 때 필요한 현금·연봉 (생애최초)</h2>
<p class="muted" style="margin:0">잔금대출을 최대로 받을 때예요. 잔금대출은 감정가 기준이라 실제 한도는 달라질 수 있어요.</p>
<table><thead><tr><th>타입</th><th class='n'>대출 최대</th><th class='n'>필요한 현금</th><th class='n'>필요 연봉</th></tr></thead><tbody>{''.join(cash_rows)}</tbody></table></section>
<section class="card"><h2>청약이 처음이라면</h2>
<p style="margin:0">청약 준비부터 계약·잔금·입주까지 순서를 <a href="{root}">열쇠까지 지도</a>에서 한 단계씩 볼 수 있어요. 공고 원문은 <a href="{e(p.get('url') or 'https://www.applyhome.co.kr')}" rel="nofollow noopener">청약홈</a>에서 확인하세요.</p></section>"""
    write(out, path, page(path, title, desc, body, root, built))
    urls.append(path)


# ---------- 출력 ----------
def write(out, path, text):
    full = os.path.join(out, *path.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "site"
    data = os.path.join(out, "data")
    if not os.path.exists(os.path.join(data, "meta.json")):
        print("data 없음 — 페이지 생성 건너뜀")
        return
    meta = json.load(open(os.path.join(data, "meta.json"), encoding="utf-8"))
    mm = re.search(r"(\d{4})-(\d{2})-(\d{2})", meta.get("built", ""))
    built = f"{int(mm.group(2))}월 {int(mm.group(3))}일" if mm else meta.get("built", "")
    lastmod = mm.group(0) if mm else None
    write(out, "p.css", CSS)
    urls = []
    n_apt, n_sgg = build_apt(data, out, built, urls)
    n_ps = build_presale(data, out, built, urls)
    lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    body = "".join(f"<url><loc>{e(BASE + u)}</loc>{lm}</url>\n" for u in [""] + urls)
    write(out, "sitemap.xml", f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}</urlset>\n')
    print(f"단지 {n_apt} · 지역 {n_sgg} · 분양 {n_ps} · 사이트맵 {len(urls) + 1}개 주소")


if __name__ == "__main__":
    main()

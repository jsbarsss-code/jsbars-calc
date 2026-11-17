"""배포 직전에 페이지에 콘텐츠 보안 정책(CSP) meta를 넣는다.

GitHub Pages는 응답 헤더를 못 바꾸므로 <meta http-equiv="Content-Security-Policy">를 쓴다.
페이지 안 <script> 블록마다 sha256 해시를 계산해 허용 목록에 넣는다 → 누가 화면에 <script>나
onerror= 같은 걸 끼워 넣어도(XSS) 브라우저가 실행하지 않는다. 외부 스크립트는 아래 호스트만 허용.

사용: python tools/add_csp.py site/index.html site/terms.html
코드를 고치면 해시는 배포 때 자동으로 다시 계산된다(손으로 관리하지 않는다).
"""
import base64
import hashlib
import re
import sys

SUPABASE = "https://wovdntrjkguewxhhccxy.supabase.co"
SCRIPT_HOSTS = [
    "https://cdn.jsdelivr.net",       # supabase-js (버전 고정 + SRI)
    "https://gc.zgo.at",              # GoatCounter count.v4.js (SRI)
    "https://t1.kakaocdn.net",        # 카카오톡 공유 SDK 2.7.2 (SRI)
    "https://dapi.kakao.com",         # 카카오 지도 SDK·장소 검색(JSONP)
    "https://t1.daumcdn.net",         # 카카오 지도 SDK 본체
]
CONNECT = ["'self'", SUPABASE, "https://jsbars.goatcounter.com", "https://dapi.kakao.com", "https://kapi.kakao.com"]   # kapi: 카카오톡 공유 메시지 검증(빠지면 공유가 4002로 실패)
IMG = ["'self'", "data:", "blob:", "https://jsbars.goatcounter.com", "https://*.kakaocdn.net", "https://*.daumcdn.net"]

INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
MARK = "<!-- csp -->"


def policy(html):
    hashes = []
    for body in INLINE.findall(html):
        h = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        hashes.append("'sha256-%s'" % h)
    script = ["'self'"] + sorted(set(hashes)) + SCRIPT_HOSTS
    if not hashes and "<script" not in html:
        script = ["'none'"]
    parts = [
        "default-src 'self'",
        "script-src " + " ".join(script),
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "font-src 'self' https://cdn.jsdelivr.net",
        "img-src " + " ".join(IMG),
        "connect-src " + " ".join(CONNECT),
        "frame-src https://*.kakao.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(parts), len(hashes)


def apply(path):
    html = open(path, encoding="utf-8").read()
    html = re.sub(re.escape(MARK) + r".*?\n", "", html)   # 다시 돌려도 한 번만 들어가게
    pol, n = policy(html)
    meta = '%s<meta http-equiv="Content-Security-Policy" content="%s">\n' % (MARK, pol)
    m = re.search(r"<meta charset=[^>]*>\s*\n?", html, re.I)
    if not m:
        raise SystemExit("charset meta 없음: " + path)
    html = html[:m.end()] + meta + html[m.end():]
    open(path, "w", encoding="utf-8", newline="").write(html)
    print("CSP: %s (inline script %d개 해시)" % (path, n))


if __name__ == "__main__":
    for f in sys.argv[1:]:
        apply(f)

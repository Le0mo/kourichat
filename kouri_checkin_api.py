import os, sys, json, time, re
from pathlib import Path
import requests

BASE = "https://api.kourichat.com"
SELF_API = "/api/user/self"
CHECKIN_API = "/api/user/clock_in"   # turnstile 查询参数

# 原始环境变量（可能包含多行）
RAW_COOKIE = os.getenv("KOURI_COOKIE", "")
RAW_UA     = os.getenv("UA", "")
VOAPI_USER = os.getenv("VOAPI_USER", "")
ACCEPT_LANGUAGE = os.getenv("ACCEPT_LANGUAGE", "zh-CN,zh-Hans;q=0.9")
TURNSTILE  = os.getenv("TURNSTILE_TOKEN", "").strip()

EXIT_OK, EXIT_UNAUTH, EXIT_FORBID, EXIT_OTHER = 0, 10, 11, 13
ART = Path("artifacts"); ART.mkdir(exist_ok=True)

def log(level, msg): print(f"[{level}] {msg}", flush=True)

def _clean_header_value(v: str) -> str:
    # 去掉 CR/LF，并把连续空白压成单空格，避免 requests 报错
    v = (v or "").replace("\r", " ").replace("\n", " ")
    v = " ".join(v.split()).strip()
    return v

def _extract_cookie(raw: str) -> str:
    """
    兼容以下输入：
    - 只含一行：'session=xxx; cf_clearance=yyy'
    - 整块抓包：包含多行 'Cookie: ...'、'Host:' 等
    - 多行里只有 'session=...' 一行
    """
    raw = raw or ""
    # 先找以 Cookie: 开头的行
    for line in raw.splitlines():
        if line.lower().startswith("cookie:"):
            return _clean_header_value(line.split(":", 1)[1])
    # 退而求其次：找包含 session= 的行
    for line in raw.splitlines():
        if "session=" in line:
            # 如果格式像 'Cookie: session=...' 上面已经返回了，这里拿纯值
            line = re.sub(r"(?i)^cookie:\s*", "", line)
            return _clean_header_value(line)
    # 没有换行就直接清理
    return _clean_header_value(raw)

def _sanitize_all():
    cookie = _extract_cookie(RAW_COOKIE)
    ua     = _clean_header_value(RAW_UA)
    vo     = _clean_header_value(VOAPI_USER)
    lang   = _clean_header_value(ACCEPT_LANGUAGE)
    if not cookie:
        log("ERROR", "缺少环境变量 KOURI_COOKIE（请粘贴浏览器里的整串 Cookie，或至少包含 session=...）")
        sys.exit(EXIT_UNAUTH)
    return cookie, ua, vo, lang

def save_art(name, content):
    try:
        p = ART / name
        if isinstance(content, (dict, list)):
            p.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            p.write_text(str(content), encoding="utf-8")
    except Exception:
        pass

def make_session(cookie: str, ua: str, voapi_user: str, lang: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ua or "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE}/profile",
        "Origin": BASE,
        "Cookie": cookie,                 # 只设置 Cookie，其它抓包头不要塞进来
        "Accept-Language": lang,
    })
    if voapi_user:
        s.headers["VoApi-User"] = voapi_user
    s.timeout = 20
    return s

def get_json(s: requests.Session, method: str, url: str, **kw):
    r = s.request(method, url, **kw)
    ts = int(time.time())
    save_art(f"{ts}_{method}_{url.split('?')[0].split('/')[-1]}_{r.status_code}.txt", r.text)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r, data

def main():
    cookie, ua, vo, lang = _sanitize_all()
    s = make_session(cookie, ua, vo, lang)

    # 1) 验证登录态
    r, data = get_json(s, "GET", BASE + SELF_API)
    save_art("self.json", data)
    if r.status_code == 401:
        log("ERROR", "未授权：Cookie 失效或未登录（401）。请重新从浏览器复制最新 Cookie。")
        sys.exit(EXIT_UNAUTH)
    if r.status_code == 403:
        log("ERROR", "被拦（403）：Cloudflare/风控。此 Cookie 与当前 Runner 的 IP/UA 不匹配。")
        sys.exit(EXIT_FORBID)
    if not r.ok:
        log("ERROR", f"/self 非预期状态码：{r.status_code}")
        sys.exit(EXIT_OTHER)
    user = data.get("data") or data.get("user") or {}
    log("INFO", f"登录有效：user={user.get('name', '-')}, id={user.get('id', '-')}")

    # 2) 签到
    qs = f"?turnstile={TURNSTILE}" if TURNSTILE else "?turnstile="
    url = BASE + CHECKIN_API + qs
    r, data = get_json(s, "GET", url)
    if r.status_code == 405:
        r, data = get_json(s, "POST", url)
    save_art("checkin.json", data)

    if r.ok:
        msg = data.get("message") or data.get("msg") or "OK"
        code = data.get("status") or data.get("code")
        log("INFO", f"签到接口返回：{code} {msg}")
        print(json.dumps(data, ensure_ascii=False))
        sys.exit(EXIT_OK)
    elif r.status_code in (401, 419):
        log("ERROR", f"签到失败（{r.status_code}）：登录态无效。请更新 Cookie。")
        sys.exit(EXIT_UNAUTH)
    elif r.status_code == 403:
        log("ERROR", "签到被拦（403）：Cloudflare/风控。建议用自托管 Runner。")
        sys.exit(EXIT_FORBID)
    else:
        log("ERROR", f"签到失败：HTTP {r.status_code}")
        print(json.dumps(data, ensure_ascii=False))
        sys.exit(EXIT_OTHER)

if __name__ == "__main__":
    main()

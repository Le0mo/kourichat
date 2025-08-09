import os, sys, json, time
from pathlib import Path
import requests

BASE = "https://api.kourichat.com"
SELF_API = "/api/user/self"
CHECKIN_API = "/api/user/clock_in"   # 用 turnstile 查询参数

# 必填：浏览器里复制的整串 Cookie（形如 "a=1; b=2; cf_clearance=...; ..."）
RAW_COOKIE = os.getenv("KOURI_COOKIE", "").strip()

# 可选：如果站点要求 turnstile token，则在 Secrets 里提供；否则留空即可
TURNSTILE = os.getenv("TURNSTILE_TOKEN", "").strip()

# 可选：自定义 UA，建议与你复制 Cookie 时的浏览器 UA 一致
UA = os.getenv("UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 退出码
EXIT_OK = 0
EXIT_UNAUTH = 10     # Cookie 失效/未登录
EXIT_FORBID = 11     # Cloudflare/403
EXIT_OTHER = 13

ART = Path("artifacts")
ART.mkdir(exist_ok=True)

def log(level, msg):
    print(f"[{level}] {msg}", flush=True)

def save_art(name, content):
    try:
        p = ART / name
        p.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def session():
    if not RAW_COOKIE:
        log("ERROR", "缺少环境变量 KOURI_COOKIE（请粘贴浏览器里的整串 Cookie）")
        sys.exit(EXIT_UNAUTH)
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE}/profile",
        "Origin": BASE,
        "Connection": "keep-alive",
        "Cookie": RAW_COOKIE,
    })
    s.timeout = 20
    return s

def get_json(s: requests.Session, method: str, url: str, **kw):
    r = s.request(method, url, **kw)
    # 保存原始响应便于排错
    ts = int(time.time())
    save_art(f"{ts}_{method}_{url.split('?')[0].split('/')[-1]}_{r.status_code}.txt", r.text)
    try:
        data = r.json()
    except Exception:
        data = {"_raw": r.text}
    return r, data

def main():
    s = session()

    # 1) 验证登录态
    r, data = get_json(s, "GET", BASE + SELF_API)
    if r.status_code == 401:
        log("ERROR", "未授权：Cookie 失效或未登录（401）。请重新从浏览器复制最新 Cookie。")
        save_art("self.json", data)
        sys.exit(EXIT_UNAUTH)
    if r.status_code == 403:
        log("ERROR", "被拦（403）：可能是 Cloudflare 绑定 IP/UA，当前 Cookie 在此环境无效。")
        save_art("self.json", data)
        sys.exit(EXIT_FORBID)
    if r.ok:
        uid = data.get("data", {}).get("id") or data.get("user", {}).get("id")
        name = data.get("data", {}).get("name") or data.get("user", {}).get("name")
        log("INFO", f"登录有效：user={name or '-'} id={uid or '-'}")
        save_art("self.json", data)
    else:
        log("ERROR", f"/self 非预期状态码：{r.status_code}")
        save_art("self.json", data)
        sys.exit(EXIT_OTHER)

    # 2) 签到（先 GET，若 405 再 POST）
    qs = f"?turnstile={TURNSTILE}" if TURNSTILE else "?turnstile="
    url = BASE + CHECKIN_API + qs

    r, data = get_json(s, "GET", url)
    if r.status_code == 405:
        r, data = get_json(s, "POST", url)

    save_art("checkin.json", data)

    if r.ok:
        # 兼容常见返回格式
        msg = data.get("message") or data.get("msg") or "OK"
        status = data.get("status") or data.get("code")
        log("INFO", f"签到接口返回：{status} {msg}")
        print(json.dumps(data, ensure_ascii=False))
        sys.exit(EXIT_OK)
    elif r.status_code in (401, 419):
        log("ERROR", f"签到失败（{r.status_code}）：登录态无效。")
        sys.exit(EXIT_UNAUTH)
    elif r.status_code == 403:
        log("ERROR", "签到被拦（403）：Cloudflare/风控。")
        sys.exit(EXIT_FORBID)
    else:
        log("ERROR", f"签到失败：HTTP {r.status_code}")
        print(json.dumps(data, ensure_ascii=False))
        sys.exit(EXIT_OTHER)

if __name__ == "__main__":
    main()

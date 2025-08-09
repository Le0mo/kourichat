import os
import sys
from pathlib import Path
from typing import Optional, Tuple, Iterable

from playwright.sync_api import sync_playwright, TimeoutError, Page, Frame

BASE_URL    = "https://api.kourichat.com"
LOGIN_URL   = f"{BASE_URL}/login"
PROFILE_URL = f"{BASE_URL}/profile"

EMAIL   = os.getenv("KOURI_EMAIL", "").strip()
PASSWORD= os.getenv("KOURI_PASS", "").strip()
DEBUG   = os.getenv("DEBUG", "0").strip()

TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "20000"))

ALLOWED_DOMAINS = {
    "qq.com", "vip.qq.com", "gmail.com", "outlook.com",
    "icloud.com", "yahoo.com", "163.com"
}

# 退出码：0=成功/已签到；1=凭证缺失/非法；2=登录失败；3=DOM/选择器失败；4=未知异常
EXIT_OK, EXIT_ENV, EXIT_LOGIN_FAIL, EXIT_DOM, EXIT_UNKNOWN = 0, 1, 2, 3, 4

ART_DIR = Path("artifacts")
ART_DIR.mkdir(exist_ok=True)


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def require_credentials() -> None:
    if not EMAIL or not PASSWORD:
        log("ERROR", "缺少环境变量 KOURI_EMAIL / KOURI_PASS")
        sys.exit(EXIT_ENV)
    mail = EMAIL.lower()
    if "@" not in mail:
        log("ERROR", f"邮箱格式不正确：{EMAIL}")
        sys.exit(EXIT_ENV)
    domain = mail.split("@", 1)[1]
    if domain not in ALLOWED_DOMAINS:
        log("ERROR", f"邮箱域名不在白名单：{domain}")
        sys.exit(EXIT_ENV)


def goto(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)


def list_frames(page: Page) -> None:
    try:
        log("INFO", f"帧数量：{len(page.frames)}")
        for i, f in enumerate(page.frames):
            log("INFO", f"frame[{i}] url={f.url!r} name={f.name!r}")
    except Exception:
        pass


def possible_toggles() -> Iterable[str]:
    # 常见需要先点一下的切换项
    return [
        "账号登录", "帐户登录", "邮箱登录", "使用密码登录", "密码登录",
        "邮箱/手机 登录", "账号密码登录",
        "Email 登录", "Use email", "Use password", "Sign in with password",
    ]


def click_toggles(ctx: Page | Frame) -> None:
    # 在当前上下文里尽力点一下切换按钮/链接，让输入框显现
    for text in possible_toggles():
        try:
            # 优先 button，再用纯文本
            btn = ctx.get_by_role("button", name=text)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=1500)
                ctx.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
                return
        except Exception:
            pass
        try:
            link = ctx.get_by_text(text, exact=False)
            if link.count() and link.first.is_visible():
                link.first.click(timeout=1500)
                ctx.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
                return
        except Exception:
            pass


def find_fields(ctx: Page | Frame) -> Tuple[Optional[object], Optional[object]]:
    """
    在给定上下文（页面或 iframe）中寻找账号与密码输入框。
    返回 (account_input, password_input)，任一找不到则返回 None。
    """
    ctx.set_default_timeout(TIMEOUT_MS)

    # 先点击可能的切换
    click_toggles(ctx)

    account_candidates = [
        ctx.get_by_label("邮箱", exact=False),
        ctx.get_by_label("电子邮箱", exact=False),
        ctx.get_by_label("Email", exact=False),
        ctx.get_by_placeholder("邮箱", exact=False),
        ctx.get_by_placeholder("邮箱/手机", exact=False),
        ctx.get_by_placeholder("账号", exact=False),
        ctx.get_by_placeholder("用户名", exact=False),
        ctx.get_by_placeholder("手机", exact=False),
        ctx.get_by_placeholder("Email", exact=False),
        ctx.locator("input[type='email']"),
        ctx.locator("input[autocomplete='username']"),
        ctx.locator("input[name='email']"),
        ctx.locator("input[name='username']"),
        ctx.locator("input[name='account']"),
        ctx.get_by_role("textbox"),
        ctx.locator("input[type='text']"),
    ]
    passwd_candidates = [
        ctx.get_by_label("密码", exact=False),
        ctx.get_by_placeholder("密码", exact=False),
        ctx.locator("input[type='password']"),
        ctx.locator("input[autocomplete='current-password']"),
    ]

    account_input = None
    for cand in account_candidates:
        try:
            n = min(cand.count(), 3)
            for i in range(n):
                el = cand.nth(i)
                if el.is_visible() and el.is_enabled():
                    account_input = el
                    break
            if account_input:
                break
        except Exception:
            continue

    passwd_input = None
    for cand in passwd_candidates:
        try:
            n = min(cand.count(), 3)
            for i in range(n):
                el = cand.nth(i)
                if el.is_visible() and el.is_enabled():
                    passwd_input = el
                    break
            if passwd_input:
                break
        except Exception:
            continue

    return account_input, passwd_input


def find_fields_anywhere(page: Page) -> Tuple[Optional[object], Optional[object], Optional[Frame]]:
    """
    在页面及其所有 iframe 中查找输入框。
    返回 (account, passwd, frame_used)。若在主页面找到，frame_used 为 None。
    """
    # 先在主页面找
    acc, pwd = find_fields(page)
    if acc and pwd:
        return acc, pwd, None

    # 再遍历 iframe
    for f in page.frames:
        try:
            acc, pwd = find_fields(f)
            if acc and pwd:
                return acc, pwd, f
        except Exception:
            continue

    return None, None, None


def fill_login_form(page: Page) -> None:
    page.set_default_timeout(TIMEOUT_MS)

    # 某些站点会先出人机验证/挑战页
    for f in page.frames:
        url = f.url.lower()
        if any(k in url for k in ["captcha", "challenge", "turnstile", "hcaptcha", "cloudflare"]):
            log("ERROR", f"检测到可能的人机验证/挑战页（frame: {f.url}），Actions 环境可能无法通过。")
            raise RuntimeError("遇到人机验证/挑战")

    account_input, passwd_input, used_frame = find_fields_anywhere(page)

    if not account_input or not passwd_input:
        # 保存证据
        try:
            page.screenshot(path=str(ART_DIR / "login_not_found.png"), full_page=True)
            Path(ART_DIR / "login_dom.html").write_text(page.content(), encoding="utf-8")
            list_frames(page)
        except Exception:
            pass
        raise RuntimeError("未定位到账号或密码输入框")

    account_input.fill(EMAIL)
    passwd_input.fill(PASSWORD)

    # 登录按钮（在相同上下文中点击）
    ctx: Page | Frame = used_frame if used_frame else page
    login_btn = (
        ctx.get_by_role("button", name="登录")
        .or_(ctx.get_by_role("button", name="Login"))
        .or_(ctx.locator("button[type='submit']"))
        .or_(ctx.locator("button:has-text('登录')"))
        .or_(ctx.locator("button:has-text('Login')"))
    )
    try:
        login_btn.first.click(timeout=5000)
    except Exception:
        passwd_input.press("Enter")

    # 等待跳转/网络稳定
    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)


def detect_already_checked(page: Page) -> bool:
    hints = [
        "已签到", "今日已打卡", "已打卡", "已完成", "已领取",
        "签到成功", "今日已签到"
    ]
    try:
        for h in hints:
            if page.locator(f"text={h}").first.is_visible():
                return True
        btn_like = page.locator("button:has-text('签到'), button:has-text('打卡')")
        if btn_like.count() > 0:
            first = btn_like.first
            if first.is_visible():
                disabled = first.get_attribute("disabled") is not None
                aria_disabled = (first.get_attribute("aria-disabled") or "").lower() in {"true", "1"}
                if disabled or aria_disabled:
                    return True
    except Exception:
        return False
    return False


def click_checkin(page: Page) -> None:
    page.set_default_timeout(TIMEOUT_MS)

    if detect_already_checked(page):
        log("INFO", "检测到已签到状态（文本提示或按钮禁用）")
        return

    candidates = [
        page.get_by_role("button", name="今日签到打卡"),
        page.locator("button:has-text('今日签到打卡')"),
        page.locator("button:has-text('签到')"),
        page.locator("button:has-text('打卡')"),
    ]

    for cand in candidates:
        try:
            if cand.count() > 0 and cand.first.is_visible():
                cand.first.click()
                page.wait_for_timeout(1200)
                log("INFO", "✅ 已点击：今日签到打卡（或同义按钮）")
                return
        except Exception:
            continue

    log("INFO", "ℹ️ 未找到签到按钮；可能今天已签或页面改版。")


def ensure_logged_in(page: Page) -> None:
    goto(page, LOGIN_URL)
    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
    log("INFO", f"到达登录页：{page.url}")
    list_frames(page)

    try:
        fill_login_form(page)
    except TimeoutError as e:
        log("ERROR", f"登录页渲染/等待超时：{e}")
        raise
    except Exception as e:
        log("ERROR", f"填充登录表单失败：{e}")
        raise

    # 访问个人页校验是否已登录
    goto(page, PROFILE_URL)
    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)
    if "login" in page.url.lower():
        raise RuntimeError("登录失败（仍在 /login 或被重定向回登录页）")

    log("INFO", f"登录成功，当前页面：{page.url}")


def main() -> None:
    require_credentials()

    headless = (DEBUG != "1")
    slow_mo = 500 if DEBUG == "1" else 0

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
            context = browser.new_context()  # 每次干净上下文
            page = context.new_page()

            ensure_logged_in(page)
            click_checkin(page)

            page.close()
            context.close()
            browser.close()
            sys.exit(EXIT_OK)

    except SystemExit:
        raise
    except TimeoutError as e:
        log("ERROR", f"超时异常：{e}")
        # 兜底保存证据
        try:
            page.screenshot(path=str(ART_DIR / "timeout.png"), full_page=True)
            Path(ART_DIR / "timeout_dom.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        sys.exit(EXIT_DOM)
    except Exception as e:
        log("ERROR", f"未知异常：{e}")
        try:
            if 'page' in locals():
                log("INFO", f"最后页面：{page.url}")
                page.screenshot(path=str(ART_DIR / "unknown.png"), full_page=True)
                Path(ART_DIR / "unknown_dom.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        sys.exit(EXIT_UNKNOWN)


if __name__ == "__main__":
    main()

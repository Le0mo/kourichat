import os
import sys
import time
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError, Page

BASE_URL     = "https://api.kourichat.com"
LOGIN_URL    = f"{BASE_URL}/login"
PROFILE_URL  = f"{BASE_URL}/profile"

EMAIL        = os.getenv("KOURI_EMAIL", "").strip()
PASSWORD     = os.getenv("KOURI_PASS", "").strip()
DEBUG        = os.getenv("DEBUG", "0").strip()

# 统一超时（毫秒）
TIMEOUT_MS   = int(os.getenv("TIMEOUT_MS", "20000"))

# 允许的邮箱域名
ALLOWED_DOMAINS = {
    "qq.com", "vip.qq.com", "gmail.com", "outlook.com", "icloud.com",
    "yahoo.com", "163.com"
}

# 退出码：0=成功/已签到；1=凭证缺失/非法；2=登录失败；3=DOM/选择器失败；4=未知异常
EXIT_OK, EXIT_ENV, EXIT_LOGIN_FAIL, EXIT_DOM, EXIT_UNKNOWN = 0, 1, 2, 3, 4


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def require_credentials() -> None:
    """检查凭证与域名白名单。失败时退出 EXIT_ENV。"""
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


def wait_dom_ready(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=TIMEOUT_MS)


def goto(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)


def on_login_page(page: Page) -> bool:
    return "login" in page.url.lower()


def fill_login_form(page: Page) -> None:
    """
    在 /login 填充并提交表单：
    - 尝试 role/name/placeholder 等多种定位
    - 提交后等待网络空闲
    """
    page.set_default_timeout(TIMEOUT_MS)

    # 尝试找账号输入框
    account_locators = [
        page.get_by_role("textbox"),
        page.locator("input[type='text']"),
        page.locator("input[name='email']"),
        page.locator("input[name='username']"),
        page.locator("input[name='account']"),
        page.locator("input[autocomplete='username']"),
        page.get_by_placeholder("邮箱", exact=False),
        page.get_by_placeholder("邮箱/手机", exact=False),
        page.get_by_placeholder("账号", exact=False),
        page.get_by_placeholder("用户名", exact=False),
        page.get_by_placeholder("手机", exact=False),
        page.locator("input[placeholder*='Email' i]"),
    ]

    account = None
    for cand in account_locators:
        try:
            # 只检查前几个候选，避免过深遍历
            count = min(cand.count(), 3)
            for i in range(count):
                el = cand.nth(i)
                if el.is_visible():
                    account = el
                    break
            if account:
                break
        except Exception:
            continue

    # 密码框
    passwd_locators = [
        page.locator("input[type='password']"),
        page.locator("input[autocomplete='current-password']"),
        page.get_by_label("密码", exact=False),
        page.get_by_placeholder("密码", exact=False),
    ]

    passwd = None
    for cand in passwd_locators:
        try:
            count = min(cand.count(), 3)
            for i in range(count):
                el = cand.nth(i)
                if el.is_visible():
                    passwd = el
                    break
            if passwd:
                break
        except Exception:
            continue

    if not account or not passwd:
        raise RuntimeError("未定位到账号或密码输入框")

    account.fill(EMAIL)
    passwd.fill(PASSWORD)

    # 登录按钮
    login_btn = (
        page.get_by_role("button", name="登录")
        .or_(page.get_by_role("button", name="Login"))
        .or_(page.locator("button[type='submit']"))
        .or_(page.locator("button:has-text('登录')"))
        .or_(page.locator("button:has-text('Login')"))
    )
    try:
        login_btn.first.click(timeout=5000)
    except Exception:
        # 回车兜底
        passwd.press("Enter")

    # 等待跳转/网络稳定
    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)


def ensure_logged_in(page: Page) -> None:
    """
    从 /login 开始尝试登录；登录后应能访问 /profile。
    若仍然停留在 /login 或再次被重定向回 /login，则视为登录失败。
    """
    goto(page, LOGIN_URL)
    wait_dom_ready(page)

    if on_login_page(page):
        log("INFO", f"到达登录页：{page.url}")
        try:
            fill_login_form(page)
        except TimeoutError as e:
            log("ERROR", f"登录页渲染/等待超时：{e}")
            sys.exit(EXIT_DOM)
        except Exception as e:
            log("ERROR", f"填充登录表单失败：{e}")
            sys.exit(EXIT_LOGIN_FAIL)

    # 尝试进入个人页校验是否已登录
    try:
        goto(page, PROFILE_URL)
        wait_dom_ready(page)
    except TimeoutError as e:
        log("ERROR", f"访问个人页超时：{e}")
        sys.exit(EXIT_DOM)

    if on_login_page(page):
        log("ERROR", "登录失败（仍在 /login 或被重定向回登录页）")
        # 可能是验证码或风控；Actions 环境下常见
        sys.exit(EXIT_LOGIN_FAIL)

    log("INFO", f"登录成功，当前页面：{page.url}")


def detect_already_checked(page: Page) -> bool:
    """
    粗略判断是否已签到：
    - 页面出现“已签到/今日已打卡/已完成”等提示
    - 按钮不可见或禁用且含“签到”字样
    """
    try:
        # 常见提示文本
        hints = [
            "已签到", "今日已打卡", "已打卡", "已完成", "已领取",
            "签到成功", "今日已签到"
        ]
        for h in hints:
            if page.locator(f"text={h}").first.is_visible():
                return True

        # 按钮存在但 disabled
        btn_like = page.locator("button:has-text('签到'), button:has-text('打卡')")
        if btn_like.count() > 0:
            first = btn_like.first
            if first.is_visible():
                # disabled 属性或 aria-disabled
                disabled = first.get_attribute("disabled") is not None
                aria_disabled = (first.get_attribute("aria-disabled") or "").lower() in {"true", "1"}
                if disabled or aria_disabled:
                    return True
    except Exception:
        # 保守处理：无法判断就返回 False
        return False
    return False


def click_checkin(page: Page) -> None:
    """
    点击“今日签到打卡”；若找不到按钮，则视为已签或页面改版，打印提示并返回 EXIT_OK。
    """
    page.set_default_timeout(TIMEOUT_MS)

    if detect_already_checked(page):
        log("INFO", "检测到已签到状态（文本提示或按钮禁用）")
        return

    # 主按钮与兜底
    candidates = [
        page.get_by_role("button", name="今日签到打卡"),
        page.locator("button:has-text('今日签到打卡')"),
        page.locator("button:has-text('签到')"),
        page.locator("button:has-text('打卡')"),
    ]

    clicked = False
    for cand in candidates:
        try:
            if cand.count() > 0:
                el = cand.first
                if el.is_visible():
                    el.click()
                    clicked = True
                    break
        except Exception:
            continue

    if clicked:
        # 等一小会儿给后端处理
        page.wait_for_timeout(1200)
        log("INFO", "✅ 已点击：今日签到打卡（或同义按钮）")
    else:
        log("INFO", "ℹ️ 未找到签到按钮；可能今天已签或页面改版。")


def main() -> None:
    require_credentials()

    headless = (DEBUG != "1")
    slow_mo = 500 if DEBUG == "1" else 0

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
            context = browser.new_context()  # 不使用 storage_state，确保每次干净登录
            page = context.new_page()

            # 登录
            ensure_logged_in(page)

            # 签到
            click_checkin(page)

            # 收尾
            page.close()
            context.close()
            browser.close()

            sys.exit(EXIT_OK)

    except SystemExit:
        raise
    except TimeoutError as e:
        log("ERROR", f"超时异常：{e}")
        sys.exit(EXIT_DOM)
    except Exception as e:
        log("ERROR", f"未知异常：{e}")
        # 输出当前 URL 以便排查
        try:
            # page 可能未创建成功
            if 'page' in locals():
                log("INFO", f"最后页面：{page.url}")
        except Exception:
            pass
        sys.exit(EXIT_UNKNOWN)


if __name__ == "__main__":
    main()

import os
import pathlib
import random
from playwright.sync_api import sync_playwright, TimeoutError

BASE_URL   = "https://api.kourichat.com"
LOGIN_URL  = f"{BASE_URL}/login"
PROFILE_URL= f"{BASE_URL}/profile"
STATE_FILE = "storage_state.json"

EMAIL    = os.getenv("KOURI_EMAIL", "")
PASSWORD = os.getenv("KOURI_PASS", "")
DEBUG    = os.getenv("DEBUG", "0")  # DEBUG=1 时可视化浏览器、慢速

def find_and_fill_login(page):
    """
    尽量通用的登录填充逻辑：
    1) 优先用 role=textbox（多数 UI 框架为 input）
    2) 其次匹配常见占位符/名称：邮箱/账号/用户名/手机/Email
    3) 密码框直接找 type=password
    """
    # 有些站点初始会跳转或异步渲染
    page.wait_for_load_state("domcontentloaded")
    if "login" not in page.url.lower():
        return False  # 已经登录或被重定向

    # 先等到任何一个可见输入框
    try:
        page.wait_for_selector("input, [contenteditable='true']", timeout=10000)
    except TimeoutError:
        raise RuntimeError("登录页未渲染出输入框，可能被风控或网络问题。")

    # 账号输入框的多策略定位（按顺序尝试）
    account_candidates = [
        # role
        page.get_by_role("textbox"),
        # 常见属性
        page.locator("input[type='text']"),
        page.locator("input[name='email']"),
        page.locator("input[name='username']"),
        page.locator("input[name='account']"),
        page.locator("input[autocomplete='username']"),
        # 常见中文/英文占位符
        page.locator("input[placeholder*='邮箱']"),
        page.locator("input[placeholder*='邮箱/手机']"),
        page.locator("input[placeholder*='账号']"),
        page.locator("input[placeholder*='用户名']"),
        page.locator("input[placeholder*='手机']"),
        page.locator("input[placeholder*='Email' i]"),
    ]

    # 密码框
    pass_candidates = [
        page.locator("input[type='password']"),
        page.locator("input[autocomplete='current-password']"),
        page.get_by_label("密码", exact=False),
        page.get_by_placeholder("密码", exact=False),
    ]

    # 选第一个可见可编辑的账号输入框
    account_input = None
    for cand in account_candidates:
        try:
            count = cand.count()
            for i in range(min(count, 3)):  # 前几个就够了
                el = cand.nth(i)
                if el.is_visible():
                    account_input = el
                    break
            if account_input:
                break
        except Exception:
            continue
    if not account_input:
        # 兜底：第一个可见 input 但不是 password
        inputs = page.locator("input:not([type='password'])")
        if inputs.count() > 0 and inputs.first.is_visible():
            account_input = inputs.first

    # 密码输入框
    pass_input = None
    for cand in pass_candidates:
        try:
            count = cand.count()
            for i in range(min(count, 3)):
                el = cand.nth(i)
                if el.is_visible():
                    pass_input = el
                    break
            if pass_input:
                break
        except Exception:
            continue

    if not account_input or not pass_input:
        raise RuntimeError("未定位到账号或密码输入框，请截图登录页让我对齐选择器。")

    if not EMAIL or not PASSWORD:
        raise RuntimeError("请设置环境变量 KOURI_EMAIL / KOURI_PASS。")

    account_input.fill(EMAIL)
    pass_input.fill(PASSWORD)

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
        # 兜底按回车
        pass_input.press("Enter")

    page.wait_for_load_state("networkidle")
    return True

def do_checkin(context):
    p = context.new_page()
    if DEBUG == "1":
        p.set_default_timeout(20000)
    p.wait_for_timeout(random.randint(500, 2000))
    p.goto(PROFILE_URL, wait_until="domcontentloaded")

    # 优先 ARIA
    try:
        p.get_by_role("button", name="今日签到打卡").wait_for(state="visible", timeout=8000)
        p.get_by_role("button", name="今日签到打卡").click()
        p.wait_for_timeout(1200)
        print("✅ 已点击：今日签到打卡")
    except Exception:
        # 兜底 CSS
        btn = p.locator('button:has-text("今日签到打卡")').first
        if btn.count() and btn.is_visible():
            btn.click()
            p.wait_for_timeout(1200)
            print("✅ 已点击（CSS 兜底）：今日签到打卡")
        else:
            print("ℹ️ 没找到“今日签到打卡”，可能今天已签或需稍后再试。")
    p.close()

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=(DEBUG != "1"), slow_mo=(500 if DEBUG == "1" else 0))
        context = browser.new_context(storage_state=STATE_FILE) if pathlib.Path(STATE_FILE).exists() else browser.new_context()

        # 打开登录页；如果已经登录会被重定向
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        if "login" in page.url.lower():
            # 尚未登录 -> 进行登录
            did = find_and_fill_login(page)
            if did:
                context.storage_state(path=STATE_FILE)
                print("✅ 登录完成并保存登录态")
        else:
            print("ℹ️ 已登录状态（未进入 /login）")

        page.close()

        # 去 /profile 签到
        do_checkin(context)

        # 保存最新 cookie
        context.storage_state(path=STATE_FILE)
        browser.close()

if __name__ == "__main__":
    main()

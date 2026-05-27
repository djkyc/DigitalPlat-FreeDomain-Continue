import os
import sys
import asyncio
import requests
import random
import json
import logging
from datetime import datetime
from urllib.parse import urljoin
from patchright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# 配置统一日志输出
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================= 配置加载 =======================
DP_ACCOUNTS_JSON = os.getenv("DP_ACCOUNTS")
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")

BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER")

ENABLE_WECHAT = os.getenv("ENABLE_WECHAT", "false").lower() == "true"
WECHAT_API_URL = os.getenv("WECHAT_API_URL")
WECHAT_AUTH_TOKEN = os.getenv("WECHAT_AUTH_TOKEN")

ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LOGIN_URL = "https://dash.domain.digitalplat.org/auth/login"
DOMAINS_URL = "https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"
BASE_URL = "https://dash.domain.digitalplat.org/"

TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 30000,
    "navigation": 60000,
    "login_wait": 180000,
}

# ======================= 账号解析 =======================
def get_accounts():
    if DP_ACCOUNTS_JSON:
        try:
            accounts = json.loads(DP_ACCOUNTS_JSON)
            if isinstance(accounts, list) and len(accounts) > 0:
                logger.info(f"使用多账号模式，共 {len(accounts)} 个账号")
                return accounts
            logger.error("DP_ACCOUNTS 必须是一个非空 JSON 数组")
            sys.exit(1)
        except json.JSONDecodeError as e:
            logger.error(f"DP_ACCOUNTS JSON 解析失败: {e}")
            sys.exit(1)
    elif DP_EMAIL and DP_PASSWORD:
        logger.info("使用单账号模式（DP_EMAIL/DP_PASSWORD）")
        return [{"email": DP_EMAIL, "password": DP_PASSWORD}]
    else:
        logger.error("未找到任何账号配置。请设置 DP_ACCOUNTS 或 DP_EMAIL+DP_PASSWORD")
        sys.exit(1)

# ======================= 统一通知模块 =======================
def send_notification(title, body, level="active", badge=None):
    if BARK_KEY:
        server_url = BARK_SERVER if BARK_SERVER else "https://api.day.app"
        api_url = f"{server_url.rstrip('/')}/{BARK_KEY}"
        try:
            payload = {"title": title, "body": body, "group": "DigitalPlat Renew", "level": level}
            if badge is not None: payload["badge"] = badge
            requests.post(api_url, json=payload, timeout=10).raise_for_status()
            logger.info("Bark 通知已发送")
        except Exception as e: logger.error(f"Bark 发送失败: {e}")

    if ENABLE_WECHAT and WECHAT_API_URL and WECHAT_AUTH_TOKEN:
        try:
            payload = {"token": WECHAT_AUTH_TOKEN, "title": title, "content": body}
            requests.post(WECHAT_API_URL, json=payload, timeout=10).raise_for_status()
            logger.info("微信通知已发送")
        except Exception as e: logger.error(f"微信通知发送失败: {e}")

    if ENABLE_TELEGRAM and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n\n{body}", "disable_web_page_preview": True}
        try:
            requests.post(url, json=payload, timeout=10).raise_for_status()
            logger.info("Telegram 通知已发送")
        except Exception as e: logger.error(f"Telegram 通知发送失败: {e}")

send_bark_notification = send_notification

# ======================= 辅助函数 =======================
def save_results(renewed_domains, failed_domains, account_email):
    safe_email = account_email.replace("@", "_").replace(".", "_")
    results = {
        "account": account_email,
        "timestamp": datetime.now().isoformat(),
        "renewed_count": len(renewed_domains),
        "failed_count": len(failed_domains),
        "renewed_domains": renewed_domains,
        "failed_domains": failed_domains,
    }
    try:
        with open(f"renewal_results_{safe_email}.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e: logger.error(f"保存结果失败: {e}")

async def retry_operation(operation, max_retries=3, delay=2, op_name="operation"):
    last_err = None
    for attempt in range(max_retries):
        try: return await operation()
        except Exception as e:
            last_err = e
            if attempt == max_retries - 1: raise
            logger.warning(f"{op_name} 失败，{delay}s 后重试... ({attempt + 1}/{max_retries})")
            await asyncio.sleep(delay)
    raise last_err

async def simulate_human_behavior(page):
    try:
        await page.mouse.move(random.randint(100, 600), random.randint(100, 600))
        await asyncio.sleep(random.uniform(0.5, 1.5))
    except Exception: pass

async def setup_browser_context(playwright):
    browser = await playwright.chromium.launch(
        headless=False,  # 🛠️ 必须保持有头模式，由 GitHub Actions 上的 Xvfb 接管
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
            "--disable-infobars",
        ],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    return browser, context

# ======================= 🔐 核心破盾函数 =======================
async def solve_cloudflare_turnstile(page):
    """
    针对国外机房高风险 IP 弹出的 Cloudflare Turnstile 验证框进行主动识别和物理点击
    """
    logger.info("正在扫描页面是否包含 Cloudflare Turnstile 人机挑战盾...")
    await page.wait_for_timeout(4000) # 给 CF 盾留出渲染时间
    
    # 定义 Turnstile 特征复选框的可能定位器
    selectors = [
        "iframe[src*='challenge-platform']",
        "div[id*='turnstile'] iframe",
        "#turnstile-widget iframe"
    ]
    
    turnstile_frame = None
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=3000)
            if element:
                turnstile_frame = await element.content_frame()
                logger.info(f"🎯 已成功捕获到人机验证组件: {selector}")
                break
        except Exception: continue

    if turnstile_frame:
        try:
            # 在 iframe 内部寻找复选框的可点击核心元素
            checkbox = await turnstile_frame.wait_for_selector(
                "input[type='checkbox'], #challenge-stage, .cb-i, .mark", 
                timeout=5000
            )
            if checkbox:
                # 获取其在 1920x1080 虚拟屏幕上的绝对物理坐标
                box = await checkbox.bounding_box()
                if box:
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + box["height"] / 2
                    logger.info(f"计算出验证框物理中心坐标: X={x}, Y={y}。正在模拟人类轨迹移动并点击...")
                    
                    # 模拟真人滑动轨迹与点击按下提起的过程
                    await page.mouse.move(x, y, steps=12)
                    await page.mouse.down()
                    await page.wait_for_timeout(random.randint(100, 200))
                    await page.mouse.up()
                    
                    logger.info("点击操作发送完毕，挂起等待 Cloudflare 放行...")
                    await page.wait_for_timeout(6000)
        except Exception as e:
            logger.warning(f"尝试模拟点击人机验证框时遇到异常: {e}")
    else:
        logger.info("未发现明显交互式阻拦盾，页面可能已自动放行。")

async def wait_for_login_form(page, email):
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            body_text = await page.inner_text("body", timeout=3000)
            if "Just a moment" in body_text or "Checking your browser" in body_text:
                logger.info(f"⏳ 检测到 Cloudflare 5秒防刷盾，静置等待...")
            elif "Access denied" in body_text or "Error 1020" in body_text:
                logger.error("🚫 糟糕，当前 GitHub Actions 分配到的机房 IP 已被该网站拉黑 (Error 1020)")
                await page.screenshot(path=f"cf_blocked_{attempt + 1}.png", full_page=True)
        except Exception: pass

        # 触发主动过盾函数
        await solve_cloudflare_turnstile(page)

        try:
            await page.wait_for_selector("input[name='email']", timeout=15000)
            logger.info("✅ 成功穿透 Cloudflare 防护层，登录表单已就绪。")
            return
        except PlaywrightTimeoutError:
            await page.screenshot(path=f"login_wait_fail_att_{attempt + 1}.png", full_page=True)
            logger.warning(f"尝试 [{attempt + 1}/{max_attempts}] 无法定位输入框，已截留现场，5秒后重试...")
            if attempt == max_attempts - 1:
                send_notification("DigitalPlat 登录失败", f"账号 {email} 无法突破人机验证盾。")
                raise Exception(f"突破人机验证拦截失败 ({email})")
            await asyncio.sleep(5)

async def login(page, email, password):
    logger.info("正在构建管道连接到登录鉴权页面...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])
    await simulate_human_behavior(page)
    await wait_for_login_form(page, email)

    logger.info("正在注入凭据...")
    await page.locator("input[name='email']").fill(email, timeout=5000)
    await page.locator("input[name='password']").fill(password, timeout=5000)
    
    await simulate_human_behavior(page)
    logger.info("提交登录表单...")
    await page.click("button[type='submit']")

    try:
        await page.wait_for_url("**/panel/main**", timeout=TIMEOUTS["navigation"])
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])

    if "/panel/main" not in page.url:
        safe_email = email.replace("@", "_").replace(".", "_")
        await page.screenshot(path=f"login_failed_{safe_email}.png")
        raise Exception(f"控制台路由跳转失败，当前位置: {page.url} ({email})")
    logger.info(f"✅ 账号 {email} 认证成功！")

# ======================= 域名处理逻辑 =======================
async def click_and_wait(page, locator, timeout=None):
    timeout = timeout or TIMEOUTS["navigation"]
    await locator.click()
    try: await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError: pass

async def collect_domain_info(page):
    rows = await page.locator("table.table-domains tbody tr").all()
    info_list = []
    for row in rows:
        try:
            onclick_attr = await row.get_attribute("onclick")
            if not onclick_attr or "'" not in onclick_attr: continue
            domain_url_path = onclick_attr.split("'")[1]
            domain_name = (await row.locator("td:nth-child(1)").inner_text()).strip()
            status = (await row.locator("td:nth-child(3)").inner_text()).strip()
            info_list.append({"name": domain_name, "path": domain_url_path, "status": status})
        except Exception as e: logger.warning(f"行数据提取异常: {e}")
    return info_list

async def process_domain(page, domain_name, domain_url_path):
    try:
        full_domain_url = urljoin(BASE_URL, domain_url_path)
        logger.info(f"正飞往资产页面: {full_domain_url}")
        await page.goto(full_domain_url, wait_until="networkidle", timeout=TIMEOUTS["navigation"])

        renew_link = page.locator("a[href*='renewdomain']")
        if await renew_link.count() == 0:
            logger.info(f"  → {domain_name} 未激活续期窗口，跳过。")
            return None, None

        logger.info(f"  → 发现续期窗口，开始执行展期链条...")
        await click_and_wait(page, renew_link.first)

        order_button = page.locator("button:has-text('Order Now'), button:has-text('Continue')").first
        if await order_button.count() == 0: return False, f"{domain_name} (缺失下单锚点)"
        await click_and_wait(page, order_button)

        agree_checkbox = page.locator("input[name='accepttos']")
        if await agree_checkbox.count() > 0:
            try: await agree_checkbox.check()
            except Exception: pass

        checkout_button = page.locator("button#checkout")
        if await checkout_button.count() == 0: return False, f"{domain_name} (缺失结算锚点)"
        await click_and_wait(page, checkout_button)

        await asyncio.sleep(3)
        page_content = await page.inner_text("body")
        if "Order Confirmation" in page_content or "successfully" in page_content.lower():
            logger.info(f"  ✅ 域名 {domain_name} 续期成功！")
            return True, None
        else:
            safe_name = domain_name.replace(".", "_")
            await page.screenshot(path=f"error_{safe_name}_confirm.png")
            return False, f"{domain_name} (确认回执异常)"

    except Exception as e:
        try:
            safe_name = domain_name.replace(".", "_")
            await page.screenshot(path=f"error_{safe_name}_exception.png")
        except Exception: pass
        return False, f"{domain_name} (异常: {str(e)})"

async def renew_for_account(account, account_index, total_accounts):
    email = account["email"]
    password = account["password"]
    logger.info(f"\n{'='*60}\n进程发起账号 [{account_index}/{total_accounts}]: {email}\n{'='*60}")

    renewed_domains = []
    failed_domains = []

    async with async_playwright() as p:
        browser, context = await setup_browser_context(p)
        page = await context.new_page()

        try:
            await retry_operation(
                lambda: login(page, email, password),
                max_retries=2, delay=6, op_name=f"Auth({email})"
            )

            logger.info("拉取全量资产路由页...")
            await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=TIMEOUTS["navigation"])
            await page.wait_for_selector("table.table-domains", timeout=TIMEOUTS["element_wait"])

            domain_info = await collect_domain_info(page)

            if not domain_info:
                logger.info("名下未检测到挂载域名。")
            else:
                logger.info(f"共发现 {len(domain_info)} 个域名资产，加入处理队列...")
                for i, info in enumerate(domain_info):
                    logger.info(f"\n进度 [{i + 1}/{len(domain_info)}] 分析目标: {info['name']} ({info['status']})")
                    success, error_msg = await process_domain(page, info["name"], info["path"])
                    if success: renewed_domains.append(info["name"])
                    elif error_msg: failed_domains.append(error_msg)

                    if i < len(domain_info) - 1:
                        try: await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=TIMEOUTS["navigation"])
                        except Exception: pass

            save_results(renewed_domains, failed_domains, email)
        except Exception as e:
            logger.error(f"账号 {email} 上层架构崩溃: {e}")
            failed_domains.append(f"管道异常: {str(e)}")
        finally:
            await context.close()
            browser.close()

    return email, renewed_domains, failed_domains

# ======================= 主入口函数 =======================
async def run_renewal():
    accounts = get_accounts()
    total = len(accounts)
    all_renewed, all_failed, account_details = [], [], []

    for idx, acc in enumerate(accounts, start=1):
        try:
            email, renewed, failed = await renew_for_account(acc, idx, total)
        except Exception as e:
            email = acc.get("email", "unknown")
            renewed, failed = [], [f"顶层异常: {str(e)}"]

        account_details.append({"email": email, "renewed": renewed, "failed": failed})
        all_renewed.extend(renewed)
        all_failed.extend(failed)

        if idx < total:
            # 账号间留出合理的冷却时间
            await asyncio.sleep(random.randint(5, 10))

    # 构建并推送汇总报告
    if not all_renewed and not all_failed:
        title = "DigitalPlat 展期检查闭环"
        body = "所有配置账号的域名状态完好，本次无可展期目标。"
    else:
        title = "DigitalPlat 自动化展期报告"
        lines = [f"✅ 成功展期数: {len(all_renewed)}", f"❌ 失败目标数: {len(all_failed)}", ""]
        for d in account_details:
            if d["renewed"] or d["failed"]:
                lines.append(f"📧 账户: {d['email']}")
                if d["renewed"]: lines.append(f"  └ 成功: {', '.join(d['renewed'])}")
                if d["failed"]: lines.append(f"  └ 失败: {', '.join(d['failed'])}")
        body = "\n".join(lines)

    send_notification(title, body)
    logger.info("🏁 完整生命周期任务执行结束。")

if __name__ == "__main__":
    asyncio.run(run_renewal())

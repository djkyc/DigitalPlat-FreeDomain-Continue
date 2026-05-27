# renew.py
# 最后更新时间: 2025-07-17
# 功能：DigitalPlat 免费域名自动续期
# 支持：多账号、Bark/微信/Telegram 通知

import os
import sys
import asyncio
import requests
import random
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================= 配置加载 =======================
# --- 账号配置（优先使用多账号 JSON，否则回退到单账号）---
DP_ACCOUNTS_JSON = os.getenv("DP_ACCOUNTS")
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")

# --- Bark 通知配置 ---
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER")          # 可选，自建 Bark 服务器

# --- 微信通知配置 ---
ENABLE_WECHAT = os.getenv("ENABLE_WECHAT", "false").lower() == "true"
WECHAT_API_URL = os.getenv("WECHAT_API_URL")
WECHAT_AUTH_TOKEN = os.getenv("WECHAT_AUTH_TOKEN")

# --- Telegram 通知配置 ---
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- 网站 URL ---
LOGIN_URL = "https://dash.domain.digitalplat.org/auth/login"
DOMAINS_URL = "https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"

# --- 超时配置 ---
TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 30000,
    "navigation": 60000,
    "login_wait": 180000
}

# ======================= 账号解析 =======================
def get_accounts():
    """获取账号列表，支持多账号 JSON 或单账号环境变量"""
    if DP_ACCOUNTS_JSON:
        try:
            accounts = json.loads(DP_ACCOUNTS_JSON)
            if isinstance(accounts, list) and len(accounts) > 0:
                logger.info(f"使用多账号模式，共 {len(accounts)} 个账号")
                return accounts
            else:
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

# ======================= 通知模块 =======================
def send_bark_notification(title, body, level="active", badge=None):
    """发送 Bark 通知"""
    if not BARK_KEY:
        return
    server_url = BARK_SERVER if BARK_SERVER else "https://api.day.app"
    api_url = f"{server_url.rstrip('/')}/{BARK_KEY}"
    try:
        payload = {"title": title, "body": body, "group": "DigitalPlat Renew", "level": level}
        if badge is not None:
            payload["badge"] = badge
        response = requests.post(api_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Bark 通知已发送")
    except Exception as e:
        logger.error(f"Bark 发送失败: {e}")

def send_wechat_notification(title, body):
    """发送微信通知（通过自定义 API）"""
    if not ENABLE_WECHAT:
        return
    if not WECHAT_API_URL or not WECHAT_AUTH_TOKEN:
        logger.warning("微信通知已启用但缺少必要配置 (WECHAT_API_URL 或 WECHAT_AUTH_TOKEN)")
        return
    try:
        # 假设接口需要 POST JSON，字段名为 token, title, content
        payload = {
            "token": WECHAT_AUTH_TOKEN,
            "title": title,
            "content": body
        }
        response = requests.post(WECHAT_API_URL, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("微信通知已发送")
    except Exception as e:
        logger.error(f"微信通知发送失败: {e}")

def send_telegram_notification(title, body):
    """发送 Telegram 通知"""
    if not ENABLE_TELEGRAM:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram 通知已启用但缺少必要配置")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"*{title}*\n\n{body}",
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Telegram 通知已发送")
    except Exception as e:
        logger.error(f"Telegram 通知发送失败: {e}")

def send_unified_notification(title, body, level="active", badge=None):
    """
    统一的推送接口：同时调用所有已启用的渠道
    """
    # Bark
    send_bark_notification(title, body, level, badge)
    # 微信
    send_wechat_notification(title, body)
    # Telegram
    send_telegram_notification(title, body)

# 为了保持与原脚本兼容，保留原函数名
def send_bark_notification_legacy(title, body, level="active", badge=None):
    """兼容旧调用，实际调用统一接口"""
    send_unified_notification(title, body, level, badge)

# 重命名，避免冲突（原脚本中所有 send_bark_notification 调用需替换为 send_unified_notification）
# 但为了最小改动，直接将原函数名指向新函数
send_bark_notification = send_unified_notification

# ======================= 辅助函数 =======================
def save_results(renewed_domains, failed_domains, account_email):
    """保存单个账号的处理结果到JSON文件（按账号区分）"""
    results = {
        "account": account_email,
        "timestamp": datetime.now().isoformat(),
        "renewed_count": len(renewed_domains),
        "failed_count": len(failed_domains),
        "renewed_domains": renewed_domains,
        "failed_domains": failed_domains
    }
    filename = f"renewal_results_{account_email.replace('@', '_')}.json"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"结果已保存至 {filename}")
    except Exception as e:
        logger.error(f"保存结果失败: {e}")

async def retry_operation(operation, max_retries=3, delay=2):
    """重试装饰器"""
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"操作失败，{delay}秒后重试... (尝试 {attempt + 1}/{max_retries})")
            await asyncio.sleep(delay)

async def simulate_human_behavior(page):
    """模拟人类行为"""
    await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
    await asyncio.sleep(random.uniform(0.5, 2))

async def setup_browser_context(playwright):
    """设置浏览器上下文"""
    browser = await playwright.firefox.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-gpu',
            '--window-size=1920,1080',
        ]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
        viewport={"width": 1920, "height": 1080}
    )
    return browser, context

async def add_anti_detection_scripts(page):
    """添加反检测脚本"""
    scripts = [
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
        "window.navigator.chrome = { runtime: {} };",
        "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});",
        "Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});"
    ]
    for script in scripts:
        await page.add_init_script(script)

async def login(page, email, password):
    """执行登录流程（使用传入的邮箱密码）"""
    logger.info("正在导航到登录页面...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])
    await simulate_human_behavior(page)

    logger.info("等待人机验证页自动跳转到登录表单...")
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            await page.wait_for_selector("input[name='email']", timeout=TIMEOUTS["login_wait"])
            logger.info("检测到登录表单，已进入账号密码输入页面。")
            break
        except PlaywrightTimeoutError:
            logger.warning(f"尝试 {attempt + 1} 失败：在{TIMEOUTS['login_wait']/1000}秒内未检测到登录输入框。")
            if attempt == max_attempts - 1:
                logger.error("所有尝试均失败，退出。")
                await page.screenshot(path="login_timeout_error.png")
                with open("login_timeout_page_source.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
                send_bark_notification(
                    "DigitalPlat 登录失败",
                    f"账号 {email} 多次尝试后未能跳过人机验证，请检查截图。",
                    level="timeSensitive"
                )
                raise Exception(f"登录失败：无法跳过人机验证 ({email})")
            await asyncio.sleep(5)

    logger.info("正在填写登录信息...")
    await page.type("input[name='email']", email, delay=random.randint(50, 150))
    await page.type("input[name='password']", password, delay=random.randint(50, 150))

    logger.info("正在点击登录按钮...")
    async with page.expect_navigation(wait_until="networkidle", timeout=TIMEOUTS["navigation"]):
        await page.click("button[type='submit']")

    if "/panel/main" not in page.url:
        logger.error(f"登录失败，当前URL为: {page.url}")
        await page.screenshot(path=f"login_failed_{email.replace('@','_')}.png")
        send_bark_notification(
            "DigitalPlat 登录失败",
            f"账号 {email} 点击登录后未能跳转到仪表盘。",
            level="timeSensitive"
        )
        raise Exception(f"登录失败：未能跳转到仪表盘 ({email})")

    logger.info(f"账号 {email} 登录成功！")

async def process_domain(page, domain_name, domain_url_path, base_url):
    """处理单个域名的续期（与原逻辑完全一致）"""
    try:
        full_domain_url = base_url + domain_url_path
        logger.info(f"正在访问 {domain_name} 的管理页面: {full_domain_url}")
        await page.goto(full_domain_url, wait_until="networkidle", timeout=TIMEOUTS["navigation"])

        renew_link = page.locator("a[href*='renewdomain']")
        if await renew_link.count() > 0:
            logger.info("找到续期链接，开始续期流程...")
            async with page.expect_navigation(wait_until="networkidle", timeout=TIMEOUTS["navigation"]):
                await renew_link.click()

            order_button = page.locator("button:has-text('Order Now'), button:has-text('Continue')").first
            if await order_button.count() > 0:
                async with page.expect_navigation(wait_until="networkidle", timeout=TIMEOUTS["navigation"]):
                    await order_button.click()

                agree_checkbox = page.locator("input[name='accepttos']")
                if await agree_checkbox.count() > 0:
                    await agree_checkbox.check()

                checkout_button = page.locator("button#checkout")
                if await checkout_button.count() > 0:
                    async with page.expect_navigation(wait_until="networkidle", timeout=TIMEOUTS["navigation"]):
                        await checkout_button.click()

                    await asyncio.sleep(2)
                    page_content = await page.inner_text("body")
                    if "Order Confirmation" in page_content or "successfully" in page_content.lower():
                        logger.info(f"成功！域名 {domain_name} 续期订单已提交。")
                        return True, None
                    else:
                        error_msg = f"{domain_name} (确认失败)"
                        logger.warning(f"域名 {domain_name} 最终确认失败")
                        await page.screenshot(path=f"error_{domain_name}_confirm.png")
                        return False, error_msg
                else:
                    error_msg = f"{domain_name} (无Checkout按钮)"
                    logger.warning(f"在 {domain_name} 的续期页面找不到 'Checkout' 按钮")
                    return False, error_msg
            else:
                error_msg = f"{domain_name} (无Order按钮)"
                logger.warning(f"在 {domain_name} 的续期页面找不到 'Order Now' 按钮")
                return False, error_msg
        else:
            logger.info("在此域名详情页未找到续期链接，可能无需续期。")
            return None, None
    except Exception as e:
        error_msg = f"{domain_name} (异常: {str(e)})"
        logger.error(f"处理域名 {domain_name} 时发生错误: {e}")
        await page.screenshot(path=f"error_{domain_name}_exception.png")
        return False, error_msg

async def renew_for_account(account, account_index, total_accounts):
    """为单个账号执行完整的续期流程"""
    email = account["email"]
    password = account["password"]
    logger.info(f"\n{'='*50}\n开始处理账号 [{account_index}/{total_accounts}]: {email}\n{'='*50}")

    renewed_domains = []
    failed_domains = []

    async with async_playwright() as p:
        browser, context = await setup_browser_context(p)
        page = await context.new_page()
        await add_anti_detection_scripts(page)

        try:
            # 登录
            await login(page, email, password)

            # 导航到域名列表
            logger.info("正在导航到域名管理页面...")
            await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=TIMEOUTS["navigation"])
            await page.wait_for_selector("table.table-domains", timeout=TIMEOUTS["element_wait"])
            logger.info("已到达域名列表页面。")

            domain_rows = await page.locator("table.table-domains tbody tr").all()
            if not domain_rows:
                logger.info("未找到任何域名。")
            else:
                logger.info(f"共找到 {len(domain_rows)} 个域名，开始逐一检查...")
                base_url = "https://dash.domain.digitalplat.org/"

                for i, row in enumerate(domain_rows):
                    onclick_attr = await row.get_attribute("onclick")
                    if onclick_attr:
                        domain_url_path = onclick_attr.split("'")[1]
                        domain_name = await row.locator("td:nth-child(1)").inner_text()
                        status = await row.locator("td:nth-child(3)").inner_text()
                        domain_name = domain_name.strip()
                        status = status.strip()
                        logger.info(f"\n[{i+1}/{len(domain_rows)}] 检查域名: {domain_name} (状态: {status})")

                        success, error_msg = await process_domain(page, domain_name, domain_url_path, base_url)
                        if success:
                            renewed_domains.append(domain_name)
                        elif error_msg:
                            failed_domains.append(error_msg)

                        # 返回域名列表页处理下一个
                        logger.info("正在返回域名列表页面...")
                        await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=TIMEOUTS["navigation"])

            # 保存该账号的结果
            save_results(renewed_domains, failed_domains, email)

        except Exception as e:
            logger.error(f"账号 {email} 处理过程中出现严重错误: {e}")
            failed_domains.append(f"整体错误: {str(e)}")
            send_bark_notification(
                f"DigitalPlat 账号处理失败 - {email}",
                f"错误信息: {str(e)}",
                level="timeSensitive"
            )
        finally:
            await browser.close()

    return email, renewed_domains, failed_domains

# ======================= 主函数 =======================
async def run_renewal():
    """主入口：循环处理所有账号，最后汇总通知"""
    accounts = get_accounts()
    total = len(accounts)

    all_renewed = []
    all_failed = []
    account_details = []

    for idx, acc in enumerate(accounts, start=1):
        email, renewed, failed = await renew_for_account(acc, idx, total)
        account_details.append({
            "email": email,
            "renewed": renewed,
            "failed": failed
        })
        all_renewed.extend(renewed)
        all_failed.extend(failed)
        # 避免请求过快，间隔5秒
        if idx < total:
            await asyncio.sleep(5)

    # ========== 发送汇总通知 ==========
    if not all_renewed and not all_failed:
        title = "DigitalPlat 续期检查完成"
        body = "所有账号的域名均已检查完毕，本次没有需要续期或处理失败的域名。"
    else:
        title = f"DigitalPlat 续期报告 (共{total}个账号)"
        lines = []
        if all_renewed:
            lines.append(f"✅ 成功续期域名总数: {len(all_renewed)}")
        if all_failed:
            lines.append(f"❌ 失败域名总数: {len(all_failed)}")
        lines.append("")
        for detail in account_details:
            if detail["renewed"] or detail["failed"]:
                lines.append(f"📧 {detail['email']}")
                if detail["renewed"]:
                    lines.append(f"  续期成功: {', '.join(detail['renewed'])}")
                if detail["failed"]:
                    lines.append(f"  失败: {', '.join(detail['failed'])}")
        body = "\n".join(lines)

    send_bark_notification(title, body)
    logger.info("所有账号处理完毕，通知已发送。")

if __name__ == "__main__":
    asyncio.run(run_renewal())

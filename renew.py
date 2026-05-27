# renew.py
# 最后更新时间: 2026-05-27
# 功能：DigitalPlat 免费域名自动续期（支持多账号、Bark/微信/Telegram 通知）
# 依赖：patchright（替代 playwright，对 Cloudflare 更友好）

import os
import sys
import asyncio
import requests
import random
import json
import logging
from datetime import datetime
from patchright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================= 配置加载 =======================
DP_ACCOUNTS_JSON = os.getenv("DP_ACCOUNTS")
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")

# Bark
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER")

# 微信
ENABLE_WECHAT = os.getenv("ENABLE_WECHAT", "false").lower() == "true"
WECHAT_API_URL = os.getenv("WECHAT_API_URL")
WECHAT_AUTH_TOKEN = os.getenv("WECHAT_AUTH_TOKEN")

# Telegram
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 网站 URL
LOGIN_URL = "https://dash.domain.digitalplat.org/auth/login"
DOMAINS_URL = "https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"
BASE_URL = "https://dash.domain.digitalplat.org/"

# 超时配置（ms）
TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 30000,
    "navigation": 60000,
    "login_wait": 180000,
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
    """统一通知入口：同时调用所有已启用的渠道"""
    # 1. Bark
    if BARK_KEY:
        server_url = BARK_SERVER if BARK_SERVER else "https://api.day.app"
        api_url = f"{server_url.rstrip('/')}/{BARK_KEY}"
        try:
            payload = {
                "title": title,
                "body": body,
                "group": "DigitalPlat Renew",
                "level": level,
            }
            if badge is not None:
                payload["badge"] = badge
            resp = requests.post(api_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Bark 通知已发送")
        except Exception as e:
            logger.error(f"Bark 发送失败: {e}")

    # 2. 微信
    if ENABLE_WECHAT:
        if WECHAT_API_URL and WECHAT_AUTH_TOKEN:
            try:
                payload = {
                    "token": WECHAT_AUTH_TOKEN,
                    "title": title,
                    "content": body,
                }
                resp = requests.post(WECHAT_API_URL, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info("微信通知已发送")
            except Exception as e:
                logger.error(f"微信通知发送失败: {e}")
        else:
            logger.warning("微信通知已启用但缺少必要配置（WECHAT_API_URL / WECHAT_AUTH_TOKEN）")

    # 3. Telegram（纯文本，避免 Markdown 特殊字符炸号）
    if ENABLE_TELEGRAM:
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": f"{title}\n\n{body}",
                "disable_web_page_preview": True,
            }
            try:
                resp = requests.post(url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info("Telegram 通知已发送")
            except Exception as e:
                logger.error(f"Telegram 通知发送失败: {e}")
        else:
            logger.warning("Telegram 通知已启用但缺少必要配置（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID）")


# 兼容旧函数名
send_bark_notification = send_notification


# ======================= 辅助函数 =======================
def save_results(renewed_domains, failed_domains, account_email):
    """保存单个账号的处理结果到 JSON 文件"""
    safe_email = account_email.replace("@", "_").replace(".", "_")
    results = {
        "account": account_email,
        "timestamp": datetime.now().isoformat(),
        "renewed_count": len(renewed_domains),
        "failed_count": len(failed_domains),
        "renewed_domains": renewed_domains,
        "failed_domains": failed_domains,
    }
    filename = f"renewal_results_{safe_email}.json"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"结果已保存至 {filename}")
    except Exception as e:
        logger.error(f"保存结果失败: {e}")


async def retry_operation(operation, max_retries=3, delay=2, op_name="operation"):
    """通用重试函数"""
    last_err = None
    for attempt in range(max_retries):
        try:
            return await operation()
        except Exception as e:
            last_err = e
            if attempt == max_retries - 1:
                logger.error(f"{op_name} 最终失败（已重试 {max_retries} 次）: {e}")
                raise
            logger.warning(
                f"{op_name} 失败，{delay}s 后重试... "
                f"(尝试 {attempt + 1}/{max_retries}) 原因: {e}"
            )
            await asyncio.sleep(delay)
    raise last_err


async def simulate_human_behavior(page):
    """模拟人类鼠标行为"""
    try:
        await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
        await asyncio.sleep(random.uniform(0.5, 2))
    except Exception:
        pass


async def setup_browser_context(playwright):
    """
    设置浏览器上下文。
    使用 patchright（已深度 patch 反检测）+ headless=False（配合 Xvfb）
    是过 Cloudflare 成功率最高的组合。
    """
    browser = await playwright.chromium.launch(
        headless=False,  # ⭐ 有头模式，配合 xvfb-run 使用
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
            "--disable-infobars",
            "--disable-extensions",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
    )
    return browser, context


# ======================= 登录相关 =======================
async def wait_for_login_form(page, email):
    """
    等待登录表单出现，过程中识别 Cloudflare 各类验证页面并记录日志/截图。
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        # 先抓一下页面文字，判断 Cloudflare 状态
        try:
            body_text = await page.inner_text("body", timeout=3000)
            if "Just a moment" in body_text or "Checking your browser" in body_text:
                logger.info(f"⏳ 检测到 Cloudflare 5秒盾，等待自动跳过... (尝试 {attempt + 1})")
            elif "Verify you are human" in body_text or "turnstile" in body_text.lower():
                logger.warning(
                    f"⚠️ 检测到 Cloudflare Turnstile 交互式挑战！"
                    f"patchright 会尝试自动处理... (尝试 {attempt + 1})"
                )
                await page.screenshot(path=f"cf_turnstile_{attempt + 1}.png", full_page=True)
            elif "Access denied" in body_text or "Error 1020" in body_text:
                logger.error("🚫 Cloudflare 直接拒绝访问（Error 1020），当前 IP 已被封锁")
                await page.screenshot(path=f"cf_blocked_{attempt + 1}.png", full_page=True)
        except Exception:
            pass

        try:
            await page.wait_for_selector(
                "input[name='email']",
                timeout=TIMEOUTS["login_wait"]
            )
            logger.info("✅ 检测到登录表单，已成功进入登录页。")
            return
        except PlaywrightTimeoutError:
            # 超时前截图保留现场
            await page.screenshot(
                path=f"login_wait_fail_{attempt + 1}.png",
                full_page=True
            )
            logger.warning(
                f"尝试 {attempt + 1} 失败：在 {TIMEOUTS['login_wait'] / 1000}s 内"
                f"未检测到登录输入框，已截图 login_wait_fail_{attempt + 1}.png"
            )
            if attempt == max_attempts - 1:
                # 保存页面源码备用
                try:
                    with open("login_timeout_page_source.html", "w", encoding="utf-8") as f:
                        f.write(await page.content())
                except Exception:
                    pass
                send_notification(
                    "DigitalPlat 登录失败",
                    f"账号 {email} 多次尝试后仍无法跳过人机验证，请下载 artifact 查看截图。",
                    level="timeSensitive",
                )
                raise Exception(f"登录失败：无法跳过人机验证 ({email})")
            await asyncio.sleep(5)


async def login(page, email, password):
    """执行登录流程"""
    logger.info("正在导航到登录页面...")
    await page.goto(
        LOGIN_URL,
        wait_until="domcontentloaded",
        timeout=TIMEOUTS["page_load"]
    )
    await simulate_human_behavior(page)

    logger.info("等待人机验证页自动跳转到登录表单...")
    await wait_for_login_form(page, email)

    logger.info("正在填写登录信息...")
    await page.type("input[name='email']", email, delay=random.randint(50, 150))
    await page.type("input[name='password']", password, delay=random.randint(50, 150))

    logger.info("正在点击登录按钮...")
    await page.click("button[type='submit']")

    # 用宽松等待替代 expect_navigation，兼容 SPA / AJAX 场景
    try:
        await page.wait_for_url("**/panel/main**", timeout=TIMEOUTS["navigation"])
    except PlaywrightTimeoutError:
        # 降级：等网络空闲
        await page.wait_for_load_state("networkidle", timeout=TIMEOUTS["navigation"])

    if "/panel/main" not in page.url:
        safe_email = email.replace("@", "_").replace(".", "_")
        logger.error(f"登录失败，当前 URL 为: {page.url}")
        await page.screenshot(path=f"login_failed_{safe_email}.png")
        send_notification(
            "DigitalPlat 登录失败",
            f"账号 {email} 点击登录后未能跳转到仪表盘，当前 URL: {page.url}",
            level="timeSensitive",
        )
        raise Exception(f"登录失败：未能跳转到仪表盘 ({email})")

    logger.info(f"✅ 账号 {email} 登录成功！")


# ======================= 域名处理 =======================
async def click_and_wait(page, locator, timeout=None):
    """点击并等待页面稳定，兼容 AJAX 和整页导航两种情况"""
    timeout = timeout or TIMEOUTS["navigation"]
    await locator.click()
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        logger.warning("等待 networkidle 超时，继续后续步骤（可能是 AJAX 页面）")


async def collect_domain_info(page):
    """
    先把所有域名信息一次性提取到普通 list。
    关键：避免后续 page.goto() 之后 locator 失效（stale element）。
    """
    rows = await page.locator("table.table-domains tbody tr").all()
    info_list = []
    for row in rows:
        try:
            onclick_attr = await row.get_attribute("onclick")
            if not onclick_attr or "'" not in onclick_attr:
                continue
            domain_url_path = onclick_attr.split("'")[1]
            domain_name = (await row.locator("td:nth-child(1)").inner_text()).strip()
            status = (await row.locator("td:nth-child(3)").inner_text()).strip()
            info_list.append({
                "name": domain_name,
                "path": domain_url_path,
                "status": status,
            })
        except Exception as e:
            logger.warning(f"提取域名行信息时出错（跳过此行）: {e}")
    return info_list


async def process_domain(page, domain_name, domain_url_path):
    """
    处理单个域名的续期。
    返回 (True, None) = 续期成功
    返回 (False, error_msg) = 续期失败
    返回 (None, None) = 无需续期
    """
    try:
        full_domain_url = BASE_URL + domain_url_path
        logger.info(f"正在访问 {domain_name} 的管理页面: {full_domain_url}")
        await page.goto(
            full_domain_url,
            wait_until="networkidle",
            timeout=TIMEOUTS["navigation"]
        )

        # 检查是否有续期链接
        renew_link = page.locator("a[href*='renewdomain']")
        if await renew_link.count() == 0:
            logger.info(f"  → {domain_name} 未找到续期链接，无需续期。")
            return None, None

        logger.info(f"  → 找到续期链接，开始续期流程...")
        await click_and_wait(page, renew_link.first)

        # Order Now / Continue 按钮
        order_button = page.locator(
            "button:has-text('Order Now'), button:has-text('Continue')"
        ).first
        if await order_button.count() == 0:
            error_msg = f"{domain_name} (无 Order 按钮)"
            logger.warning(f"  → 找不到 'Order Now' 按钮")
            return False, error_msg
        await click_and_wait(page, order_button)

        # 服务条款复选框
        agree_checkbox = page.locator("input[name='accepttos']")
        if await agree_checkbox.count() > 0:
            try:
                await agree_checkbox.check()
                logger.info("  → 已勾选服务条款")
            except Exception as e:
                logger.warning(f"  → 勾选服务条款时出错（可能已勾选）: {e}")

        # Checkout 按钮
        checkout_button = page.locator("button#checkout")
        if await checkout_button.count() == 0:
            error_msg = f"{domain_name} (无 Checkout 按钮)"
            logger.warning(f"  → 找不到 'Checkout' 按钮")
            return False, error_msg
        await click_and_wait(page, checkout_button)

        # 等待确认并判断结果
        await asyncio.sleep(2)
        page_content = await page.inner_text("body")
        if "Order Confirmation" in page_content or "successfully" in page_content.lower():
            logger.info(f"  ✅ 域名 {domain_name} 续期成功！")
            return True, None
        else:
            safe_name = domain_name.replace(".", "_")
            await page.screenshot(path=f"error_{safe_name}_confirm.png")
            error_msg = f"{domain_name} (确认页面异常)"
            logger.warning(f"  → 域名 {domain_name} 最终确认失败，已截图")
            return False, error_msg

    except Exception as e:
        error_msg = f"{domain_name} (异常: {str(e)})"
        logger.error(f"  → 处理域名 {domain_name} 时发生异常: {e}")
        try:
            safe_name = domain_name.replace(".", "_")
            await page.screenshot(path=f"error_{safe_name}_exception.png")
        except Exception:
            pass
        return False, error_msg


# ======================= 账号级主流程 =======================
async def renew_for_account(account, account_index, total_accounts):
    """为单个账号执行完整的续期流程"""
    email = account["email"]
    password = account["password"]
    logger.info(
        f"\n{'=' * 50}\n"
        f"开始处理账号 [{account_index}/{total_accounts}]: {email}\n"
        f"{'=' * 50}"
    )

    renewed_domains = []
    failed_domains = []

    async with async_playwright() as p:
        browser, context = await setup_browser_context(p)
        page = await context.new_page()
        # ⭐ patchright 自带深度反检测，不需要手动注入脚本

        try:
            # 登录（带重试）
            await retry_operation(
                lambda: login(page, email, password),
                max_retries=2,
                delay=5,
                op_name=f"login({email})",
            )

            # 导航到域名列表
            logger.info("正在导航到域名管理页面...")
            await page.goto(
                DOMAINS_URL,
                wait_until="networkidle",
                timeout=TIMEOUTS["navigation"]
            )
            await page.wait_for_selector(
                "table.table-domains",
                timeout=TIMEOUTS["element_wait"]
            )
            logger.info("✅ 已到达域名列表页面。")

            # ⭐ 关键修复：先一次性提取所有域名信息，避免 stale locator
            domain_info = await collect_domain_info(page)

            if not domain_info:
                logger.info("未找到任何域名。")
            else:
                logger.info(f"共找到 {len(domain_info)} 个域名，开始逐一检查...")
                for i, info in enumerate(domain_info):
                    logger.info(
                        f"\n[{i + 1}/{len(domain_info)}] "
                        f"检查域名: {info['name']} (状态: {info['status']})"
                    )
                    success, error_msg = await process_domain(
                        page, info["name"], info["path"]
                    )
                    if success:
                        renewed_domains.append(info["name"])
                    elif error_msg:
                        failed_domains.append(error_msg)

                    # 每个域名处理完后回到列表页
                    if i < len(domain_info) - 1:
                        logger.info("正在返回域名列表页面...")
                        try:
                            await page.goto(
                                DOMAINS_URL,
                                wait_until="networkidle",
                                timeout=TIMEOUTS["navigation"],
                            )
                        except Exception as e:
                            logger.warning(f"返回列表页失败（继续下一个域名）: {e}")

            save_results(renewed_domains, failed_domains, email)

        except Exception as e:
            logger.error(f"账号 {email} 处理过程中出现严重错误: {e}")
            failed_domains.append(f"整体错误: {str(e)}")
            send_notification(
                f"DigitalPlat 账号处理失败 - {email}",
                f"错误信息: {str(e)}",
                level="timeSensitive",
            )
        finally:
            await context.close()
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
        try:
            email, renewed, failed = await renew_for_account(acc, idx, total)
        except Exception as e:
            email = acc.get("email", "unknown")
            renewed, failed = [], [f"账号级异常: {str(e)}"]
            logger.error(f"账号 {email} 顶层异常: {e}")

        account_details.append({"email": email, "renewed": renewed, "failed": failed})
        all_renewed.extend(renewed)
        all_failed.extend(failed)

        # 多账号之间间隔，避免请求过快
        if idx < total:
            logger.info("等待 5 秒后处理下一个账号...")
            await asyncio.sleep(5)

    # ===================== 汇总通知 =====================
    if not all_renewed and not all_failed:
        title = "DigitalPlat 续期检查完成"
        body = "所有账号的域名均已检查完毕，本次没有需要续期或处理失败的域名。"
    else:
        title = f"DigitalPlat 续期报告（共 {total} 个账号）"
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

    send_notification(title, body)
    logger.info("✅ 所有账号处理完毕，通知已发送。")


if __name__ == "__main__":
    asyncio.run(run_renewal())

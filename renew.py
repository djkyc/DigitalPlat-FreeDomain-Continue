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

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载配置
DP_ACCOUNTS_JSON = os.getenv("DP_ACCOUNTS")
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

LOGIN_URL = "https://dash.domain.digitalplat.org/auth/login"
DOMAINS_URL = "https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"
BASE_URL = "https://dash.domain.digitalplat.org/"

# ======================= 🔐 独立通知模块（彻底修复爆栈） =======================
def send_system_notification(title, body):
    """
    纯粹、绝对独立的通知渠道，杜绝一切函数自调用递归风险
    """
    if BARK_KEY:
        server = BARK_SERVER if BARK_SERVER else "https://api.day.app"
        try:
            requests.post(f"{server.rstrip('/')}/{BARK_KEY}", json={"title": title, "body": body, "group": "DigitalPlat"}, timeout=10)
            logger.info("📡 Bark 通知成功下发")
        except Exception as e: logger.error(f"Bark 发送失败: {e}")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n\n{body}"}, timeout=10)
            logger.info("📡 Telegram 通知成功下发")
        except Exception as e: logger.error(f"Telegram 发送失败: {e}")

# ======================= 🛡️ 高级指纹隐藏与环境初始化 =======================
def get_accounts():
    if DP_ACCOUNTS_JSON:
        try: return json.loads(DP_ACCOUNTS_JSON)
        except Exception as e: logger.error(f"JSON 解析失败: {e}"); sys.exit(1)
    elif DP_EMAIL and DP_PASSWORD:
        return [{"email": DP_EMAIL, "password": DP_PASSWORD}]
    logger.error("未找到账号环境变量"); sys.exit(1)

async def setup_stealth_context(playwright):
    """
    深度注入隐蔽参数，抹除 GitHub Actions 自动化测试特征
    """
    browser = await playwright.chromium.launch(
        headless=False, # 配合 xvfb 仿真
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled", # 核心：抹除 navigator.webdriver
            "--window-size=1920,1080",
            "--no-first-run",
            "--no-service-autorun",
            "--password-store=basic"
        ]
    )
    
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN,zh;q=0.9,en;q=0.8",
        timezone_id="Asia/Shanghai",
        accept_downloads=False
    )
    
    # 注入标准的现代浏览器指纹特征，防止被 CF 识别为无头节点
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    """)
    return browser, context

# ======================= 🎯 物理鼠标模拟与主动破盾 =======================
async def human_mouse_move_and_click(page, x, y):
    """
    模拟人类使用鼠标的物理轨迹（贝塞尔平滑滑动），规避 CF 鼠标瞬间瞬移检测
    """
    current_x, current_y = 100, 100 # 初始起点
    steps = 15
    for i in range(steps):
        # 动态计算逼近平滑曲线
        tween_x = current_x + (x - current_x) * (i / steps) + random.randint(-3, 3)
        tween_y = current_y + (y - current_y) * (i / steps) + random.randint(-3, 3)
        await page.mouse.move(tween_x, tween_y)
        await asyncio.sleep(0.02)
    
    # 到达目标点，执行物理点击
    await page.mouse.move(x, y)
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.1, 0.25))
    await page.mouse.up()

async def solve_cloudflare_turnstile(page):
    """
    寻找页面中的 Cloudflare Turnstile 验证框并物理点击
    """
    logger.info("🕵️ 正在扫描页面是否包含交互式 Turnstile 验证组件...")
    await page.wait_for_timeout(5000) # 留给 CF 盾加载和初始重定向平稳的时间
    
    selectors = [
        "iframe[src*='challenge-platform']",
        "div[id*='turnstile'] iframe",
        "#turnstile-widget iframe"
    ]
    
    turnstile_frame = None
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=4000)
            if element:
                turnstile_frame = await element.content_frame()
                logger.info(f"🎯 成功定位验证组件 Iframe: {selector}")
                break
        except Exception: continue

    if turnstile_frame:
        try:
            # 锁定复选框元素
            checkbox = await turnstile_frame.wait_for_selector(
                "input[type='checkbox'], #challenge-stage, .cb-i, .mark", 
                timeout=6000
            )
            if checkbox:
                box = await checkbox.bounding_box()
                if box:
                    # 计算绝对坐标
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + box["height"] / 2
                    logger.info(f"📍 验证框坐标已就绪 (X={int(x)}, Y={int(y)})，开始模拟物理鼠标动作过盾...")
                    await human_mouse_move_and_click(page, x, y)
                    
                    logger.info("⏳ 动作完成，静置等待 Cloudflare 验证通过跳转...")
                    await page.wait_for_timeout(8000)
                    return True
        except Exception as e:
            logger.warning(f"模拟点击人机验证框异常: {e}")
    else:
        logger.info("未发现显式阻拦盾，页面可能已直接放行。")
    return False

# ======================= 🛒 自动化业务主链路 =======================
async def login_and_bypass(page, email, password):
    logger.info(f"正在建立连线并导航至: {LOGIN_URL}")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    
    # 循环检查和攻关 CF 拦截页
    for attempt in range(1, 4):
        logger.info(f"🔄 正在尝试解除拦截状态 (轮次 {attempt}/3)...")
        body_text = ""
        try: body_text = await page.inner_text("body", timeout=3000)
        except Exception: pass

        if "Access denied" in body_text or "Error 1020" in body_text:
            await page.screenshot(path="error_1020_blocked.png", full_page=True)
            raise Exception("❌ 该 GitHub 节点的 IP 已经被 Cloudflare 彻底拉黑封禁。")

        # 调用物理鼠标过盾
        await solve_cloudflare_turnstile(page)
        
        # 检查表单输入框是否已经渲染出来
        try:
            await page.wait_for_selector("input[name='email']", timeout=12000)
            logger.info("🎉 完美！输入框就绪，Cloudflare 防护墙已成功穿透。")
            break
        except PlaywrightTimeoutError:
            await page.screenshot(path=f"cf_stage_debug_att_{attempt}.png")
            if attempt == 3:
                raise Exception("❌ 无法通过人机阻拦盾，请检查 artifacts 调试截图。")

    # 注入凭据登录
    await page.locator("input[name='email']").fill(email, timeout=5000)
    await page.locator("input[name='password']").fill(password, timeout=5000)
    await page.wait_for_timeout(1000)
    await page.click("button[type='submit']")

    try:
        await page.wait_for_url("**/panel/main**", timeout=30000)
        logger.info(f"✅ 账号 {email} 认证登录成功！")
    except PlaywrightTimeoutError:
        await page.screenshot(path="login_submit_failed.png")
        raise Exception(f"控制台路由重定向超时，当前位置: {page.url}")

async def process_renewal_workflow(page):
    """
    进入主面板处理续期点击
    """
    logger.info("正在调取全量域名资产列表...")
    await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=45000)
    await page.wait_for_selector("table.table-domains", timeout=20000)

    # 抓取表格中所有行
    rows = await page.locator("table.table-domains tbody tr").all()
    domain_targets = []
    
    for row in rows:
        try:
            onclick_attr = await row.get_attribute("onclick")
            if onclick_attr and "'" in onclick_attr:
                path = onclick_attr.split("'")[1]
                name = (await row.locator("td:nth-child(1)").inner_text()).strip()
                domain_targets.append({"name": name, "path": path})
        except Exception: continue

    renewed, failed = [], []
    if not domain_targets:
        logger.info("名下暂无绑定的域名资产。")
        return renewed, failed

    logger.info(f"共盘点到 {len(domain_targets)} 个资产，开始轮询续期链条...")
    for i, target in enumerate(domain_targets):
        name = target["name"]
        path = target["path"]
        logger.info(f"分析目标 [{i+1}/{len(domain_targets)}]: {name}")
        
        try:
            await page.goto(urljoin(BASE_URL, path), wait_until="networkidle", timeout=30000)
            renew_link = page.locator("a[href*='renewdomain']")
            
            if await renew_link.count() == 0:
                logger.info(f"  → 域名 {name} 未满足展期窗口要求，跳过。")
                continue
                
            await renew_link.first.click()
            await page.wait_for_load_state("networkidle")
            
            order_btn = page.locator("button:has-text('Order Now'), button:has-text('Continue')").first
            await order_btn.click()
            await page.wait_for_load_state("networkidle")
            
            tos = page.locator("input[name='accepttos']")
            if await tos.count() > 0: await tos.check()
            
            await page.click("button#checkout")
            await page.wait_for_timeout(4000)
            
            content = await page.inner_text("body")
            if "Order Confirmation" in content or "successfully" in content.lower():
                logger.info(f"  ✅ {name} 续期展期成功！")
                renewed.append(name)
            else:
                failed.append(f"{name} (确认页异常)")
        except Exception as e:
            failed.append(f"{name} (异常: {str(e)})")
            
        # 页面归位回到列表
        await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=20000)
        
    return renewed, failed

async def run_renewal_task():
    accounts = get_accounts()
    summary_lines = []
    
    async with async_playwright() as p:
        browser, context = await setup_stealth_context(p)
        page = await context.new_page()
        
        for idx, acc in enumerate(accounts, start=1):
            email = acc["email"]
            password = acc["password"]
            logger.info(f"\n开启多账号处理链 [{idx}/{len(accounts)}]: {email}")
            
            try:
                # 执行穿透登录
                await login_and_bypass(page, email, password)
                # 执行续期
                ren, fa = await process_workflow(page) if 'process_workflow' in globals() else await process_renewal_workflow(page)
                
                summary_lines.append(f"📧 账户: {email}")
                if ren: summary_lines.append(f"  └ ✅ 成功: {', '.join(ren)}")
                if fa:  summary_lines.append(f"  └ ❌ 失败: {', '.join(fa)}")
            except Exception as e:
                logger.error(f"❌ 账户 {email} 处理失败: {e}")
                summary_lines.append(f"📧 账户: {email}\n  └ 💥 任务溃败: {str(e)}")
            
            if idx < len(accounts): await asyncio.sleep(10)
            
        await context.close()
        browser.close()

    # 发送通知
    if summary_lines:
        send_system_notification("DigitalPlat 域名续期全盘报告", "\n".join(summary_lines))
    logger.info("🏁 业务闭环，所有程序退出。")

if __name__ == "__main__":
    asyncio.run(run_renewal_task())

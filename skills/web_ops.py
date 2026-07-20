"""WebOps skill — let the agent actually operate web pages.

Inspired by the Kiki (腾讯云智能助手) pattern: when a user asks for
something concrete on a web site ("帮我领 60+ 款腾讯云产品的免费试用资格"),
the agent should be able to:

  1. Open the page in a real browser
  2. Read the page (headings, forms, key text)
  3. Click buttons / fill inputs / submit
  4. Report what it found and what it did

The skill wraps a **single shared** Playwright Chromium instance using
the **async API** so it never blocks the FastAPI event loop. Tools are
`async def` so langgraph's `astream` can `await` them concurrently.
Pages are per-tenant isolated through distinct `BrowserContext`s.

Tools exposed:
  - web_open_url(url)                   -> page title + first 1500 chars
  - web_extract_text(limit?)            -> full visible text (truncated)
  - web_list_links()                    -> all <a> hrefs (text -> url)
  - web_click(target, by?)              -> click and return new page text
  - web_fill(selector, value)           -> fill a form field
  - web_press_key(key)                  -> press keyboard key
  - web_wait_for(target, by?, t?)       -> wait up to N seconds
  - web_screenshot(name?)               -> save PNG, return path
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from config import settings
from skills.base import Skill

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Browser pool (single shared Chromium, async API, per-tenant contexts)
# --------------------------------------------------------------------------- #
# We use playwright.async_api (NOT sync_api) because the agent runs inside
# an async event loop (FastAPI + langgraph astream). The sync API would
# block the entire event loop on every page.goto() / page.click() call,
# freezing all concurrent SSE streams. The async API yields control back
# to the loop while waiting for the browser, so other requests keep flowing.
_BROWSER: Any | None = None  # playwright.async_api.Browser
_PLAYWRIGHT: Any | None = None  # playwright.async_api.Playwright
_CONTEXTS: dict[str, Any] = {}  # tenant_id -> BrowserContext
_INIT_LOCK: asyncio.Lock | None = None  # lazily created (loop may not exist at import)


def _init_lock() -> asyncio.Lock:
    """Create the init lock lazily — asyncio.Lock() needs a running loop."""
    global _INIT_LOCK
    if _INIT_LOCK is None:
        _INIT_LOCK = asyncio.Lock()
    return _INIT_LOCK


async def _ensure_browser() -> Any:
    """Lazy-start a single shared Chromium. Returns the Browser handle."""
    global _BROWSER, _PLAYWRIGHT
    if _BROWSER is not None:
        return _BROWSER
    async with _init_lock():
        if _BROWSER is not None:
            return _BROWSER
        # Import inside the function so test environments without
        # playwright still import this module.
        from playwright.async_api import async_playwright

        logger.info("WebOpsSkill: launching shared Chromium (async)")
        _PLAYWRIGHT = await async_playwright().start()
        _BROWSER = await _PLAYWRIGHT.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        return _BROWSER


async def _get_context(tenant_id: str) -> Any:
    """Per-tenant BrowserContext. Created on first use, kept warm."""
    if tenant_id in _CONTEXTS:
        return _CONTEXTS[tenant_id]
    browser = await _ensure_browser()
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
    )
    _CONTEXTS[tenant_id] = ctx
    logger.info("WebOpsSkill: created BrowserContext for tenant=%s", tenant_id)
    return ctx


async def _get_page(tenant_id: str) -> tuple[Any, Any]:
    """Return (context, page). Reuse the most recent page or open a new one."""
    ctx = await _get_context(tenant_id)
    if ctx.pages:
        return ctx, ctx.pages[-1]
    page = await ctx.new_page()
    return ctx, page


def _tenant_id() -> str:
    """Read tenant id from the same contextvar that commerce tools use."""
    from skills.commerce import current_tenant_id

    return current_tenant_id.get()


def _truncate(text: str, limit: int = 1500) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, total {len(text)} chars)"


# --------------------------------------------------------------------------- #
# Tools (all async — langgraph astream awaits them without blocking the loop)
# --------------------------------------------------------------------------- #
@tool
async def web_open_url(url: str) -> str:
    """Open a URL in the browser and return the page title + visible text.

    Use this as the FIRST step for any web task. After open_url succeeds
    you can call web_extract_text / web_list_links / web_click / web_fill
    to interact with the page. Provide a fully-qualified URL starting
    with http:// or https://.
    """
    if not url or not url.startswith(("http://", "https://")):
        return f"操作未通过：URL 必须以 http:// 或 https:// 开头，收到 {url!r}"
    try:
        _, page = await _get_page(_tenant_id())
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = await page.title()
        text = _truncate(await page.inner_text("body"))
        return f"● 已打开：{url}\n\n**页面标题**：{title}\n\n**正文摘要**：\n{text}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_open_url failed")
        return f"操作未通过：无法打开 {url}（{type(exc).__name__}: {exc}）"


@tool
async def web_extract_text(limit: int = 3000) -> str:
    """Read the currently-open page's visible text (top-down, length-limited).

    Use after web_open_url to read a section of the page. Default 3000
    chars; pass a higher limit to read more (max 10000).
    """
    limit = max(100, min(int(limit or 3000), 10000))
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        return _truncate(await page.inner_text("body"), limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_extract_text failed")
        return f"操作未通过：读取页面失败（{type(exc).__name__}: {exc}）"


@tool
async def web_list_links() -> str:
    """List every link on the current page as `text -> url`.

    Use this to discover which buttons or menu items exist before
    calling web_click. Output is truncated to the first 60 links.
    """
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0, 60).map(e => ({t: (e.innerText||'').trim().slice(0,40), h: e.href}))",
        )
        if not links:
            return "当前页面没有 <a> 链接。"
        lines = [f"- {lk['t'] or '(无文本)'} -> {lk['h']}" for lk in links]
        return "**页面链接清单**：\n" + "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_list_links failed")
        return f"操作未通过：读取链接失败（{type(exc).__name__}: {exc}）"


@tool
async def web_click(target: str, by: str = "text") -> str:
    """Click an element on the current page.

    Args:
        target: The button/link text or CSS selector to click.
        by: Either "text" (match by visible text, recommended) or
            "selector" (treat target as a CSS selector).
    """
    by = (by or "text").lower()
    if by not in {"text", "selector"}:
        return f"操作未通过：by 必须是 'text' 或 'selector'，收到 {by!r}"
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        if by == "text":
            # Playwright text= selector: case-insensitive substring match
            loc = page.get_by_text(target, exact=False).first
            if await loc.count() == 0:
                return f"操作未通过：未找到文本包含 {target!r} 的可点击元素。"
            await loc.click(timeout=5000)
        else:
            await page.locator(target).first.click(timeout=5000)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        title = await page.title()
        text = _truncate(await page.inner_text("body"), limit=800)
        return f"● 已点击：{target!r}\n\n**当前标题**：{title}\n\n**页面摘要**：\n{text}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_click failed")
        return f"操作未通过：点击 {target!r} 失败（{type(exc).__name__}: {exc}）"


@tool
async def web_fill(selector: str, value: str) -> str:
    """Fill a form field (input / textarea) identified by a CSS selector.

    After filling, you typically want to call web_click to submit the
    form. Use simple selectors like '#email', 'input[name=phone]', or
    'textarea#message'.
    """
    if not selector or not value:
        return "操作未通过：selector 和 value 都必填。"
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return f"操作未通过：未找到选择器 {selector!r} 对应的元素。"
        await loc.fill(value)
        return f"● 已填写：{selector!r} = {value!r}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_fill failed")
        return f"操作未通过：填写 {selector!r} 失败（{type(exc).__name__}: {exc}）"


@tool
async def web_press_key(key: str) -> str:
    """Press a keyboard key (e.g. "Enter", "Escape", "Tab", "ArrowDown").

    Use after web_fill to submit a form (Enter), or to dismiss a modal
    (Escape). The key is sent to the currently-focused element.
    """
    if not key:
        return "操作未通过：key 必填，例如 'Enter' / 'Escape'。"
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        await page.keyboard.press(key)
        return f"● 已按键：{key!r}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_press_key failed")
        return f"操作未通过：按键 {key!r} 失败（{type(exc).__name__}: {exc}）"


@tool
async def web_wait_for(target: str, by: str = "text", timeout: int = 8) -> str:
    """Wait up to `timeout` seconds for an element / text to appear.

    Useful when the page does an async redirect or loads content after
    a click. by="text" (default) waits for visible text. by="selector"
    waits for a CSS selector.
    """
    by = (by or "text").lower()
    timeout_s = max(1, min(int(timeout or 8), 30))
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        if by == "selector":
            await page.wait_for_selector(target, timeout=timeout_s * 1000)
        else:
            await page.get_by_text(target, exact=False).first.wait_for(
                timeout=timeout_s * 1000
            )
        return f"● 已等到：{target!r}（最多等了 {timeout_s}s）"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_wait_for failed")
        return f"操作未通过：等待 {target!r} 超时（{type(exc).__name__}: {exc}）"


@tool
async def web_screenshot(name: str = "screenshot") -> str:
    """Save a PNG screenshot of the current page under data/webops/<name>.png.

    The returned path is a server-side filesystem path; the API layer
    serves it back to the browser via /api/webops/screenshots/<name>.
    """
    # Sanitize the name: alphanum / dash / underscore only.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "screenshot"))
    safe = safe[:64] or "screenshot"
    out_dir = Path(settings.vector_store_dir).parent / "webops_screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe}.png"
    try:
        _, page = await _get_page(_tenant_id())
        if not page.url or page.url == "about:blank":
            return "操作未通过：当前未打开任何页面，请先调用 web_open_url。"
        await page.screenshot(path=str(out_path), full_page=False)
        return f"● 已截图：{out_path}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("web_screenshot failed")
        return f"操作未通过：截图失败（{type(exc).__name__}: {exc}）"


# --------------------------------------------------------------------------- #
# Skill registration
# --------------------------------------------------------------------------- #
class WebOpsSkill(Skill):
    """Browser-automation skill. Lets the agent drive a real Chromium."""

    name = "web_ops"
    description = (
        "驱动一个真实的 Chromium 浏览器，让 agent 打开网页、阅读内容、"
        "点击按钮、填写表单、截图。适合需要'现场看看'或'帮我操作'的请求。"
    )
    version = "0.1.0"
    tags = ("web", "automation", "browser")
    permissions = ("network:outbound",)
    dependencies = ("playwright>=1.60",)
    enabled_by_default = True

    def build_tools(self) -> list[Any]:
        return [
            web_open_url,
            web_extract_text,
            web_list_links,
            web_click,
            web_fill,
            web_press_key,
            web_wait_for,
            web_screenshot,
        ]

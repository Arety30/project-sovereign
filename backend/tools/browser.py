import os
import base64
import logging
from bs4 import BeautifulSoup
from typing import Optional

logger = logging.getLogger("sovereign.tools.browser")

# Global instances for reuse across tool invocations
_playwright_ctx = None
_browser = None
_context = None
_page = None

# Ensure the frontend folder exists for saving screenshots
SCREENSHOT_DIR = "~/sovereign_agent/frontend"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
SCREENSHOT_PATH = os.path.join(SCREENSHOT_DIR, "live_view.png")

async def get_browser_page(headed: bool = False):
    """Initializes and returns the shared browser page context."""
    global _playwright_ctx, _browser, _context, _page
    
    if _page is not None and not _page.is_closed():
        return _page
        
    logger.info("Initializing Playwright and Chromium instance...")
    from playwright.async_api import async_playwright
    
    if _playwright_ctx is None:
        _playwright_ctx = await async_playwright().start()
        
    if _browser is None:
        exec_path = None
        for path in ["/usr/bin/chromium-browser", "/snap/bin/chromium", "/usr/bin/google-chrome"]:
            if os.path.exists(path):
                exec_path = path
                logger.info(f"Detected system browser path: {exec_path}")
                break
                
        _browser = await _playwright_ctx.chromium.launch(
            headless=not headed,
            executable_path=exec_path,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        
    if _context is None:
        _context = await _browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        
    _page = await _context.new_page()
    # Set default navigation timeout
    _page.set_default_navigation_timeout(20000)
    _page.set_default_timeout(10000)
    return _page

async def close_browser():
    """Cleans up browser processes."""
    global _playwright_ctx, _browser, _context, _page
    logger.info("Closing browser process...")
    try:
        if _page:
            await _page.close()
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _playwright_ctx:
            await _playwright_ctx.stop()
    except Exception as e:
        logger.error(f"Error during browser cleanup: {e}")
    finally:
        _page = None
        _context = None
        _browser = None
        _playwright_ctx = None

async def _save_screenshot(page) -> str:
    """Internal helper to capture page screenshot for UI stream."""
    try:
        await page.screenshot(path=SCREENSHOT_PATH)
        return SCREENSHOT_PATH
    except Exception as e:
        logger.error(f"Failed to capture screenshot: {e}")
        return ""

async def browser_navigate(url: str) -> str:
    """Navigates to the specified URL."""
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
        # Auto-prefix http if missing
        url = "http://" + url
        
    try:
        page = await get_browser_page()
        logger.info(f"Navigating browser to: {url}")
        response = await page.goto(url, wait_until="domcontentloaded")
        await _save_screenshot(page)
        
        status = response.status if response else "Unknown"
        title = await page.title()
        return f"Successfully navigated to {url} (Status: {status}, Title: '{title}'). Screenshot captured."
    except Exception as e:
        return f"Error navigating to {url}: {str(e)}"

async def browser_click(selector: str) -> str:
    """Clicks on a page element matched by CSS selector."""
    try:
        page = await get_browser_page()
        # Wait for element to be visible
        await page.wait_for_selector(selector, state="visible", timeout=5000)
        # Click
        await page.click(selector)
        await page.wait_for_timeout(1000) # Let page react
        await _save_screenshot(page)
        return f"Successfully clicked element '{selector}'. Screenshot captured."
    except Exception as e:
        return f"Error clicking element '{selector}': {str(e)}"

async def browser_type(selector: str, text: str, press_enter: bool = True) -> str:
    """Types text into an input field. Optionally presses Enter."""
    try:
        page = await get_browser_page()
        await page.wait_for_selector(selector, state="visible", timeout=5000)
        # Focus & Type
        await page.focus(selector)
        await page.fill(selector, "")
        await page.type(selector, text, delay=50)
        
        if press_enter:
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1500)
            
        await _save_screenshot(page)
        return f"Successfully typed '{text}' into '{selector}' (Press Enter: {press_enter}). Screenshot captured."
    except Exception as e:
        return f"Error typing into '{selector}': {str(e)}"

async def browser_scroll(direction: str = "down") -> str:
    """Scrolls the current page up or down."""
    try:
        page = await get_browser_page()
        if direction.lower() == "down":
            await page.evaluate("window.scrollBy(0, 400)")
        elif direction.lower() == "up":
            await page.evaluate("window.scrollBy(0, -400)")
        else:
            return f"Error: Invalid scroll direction '{direction}'. Use 'up' or 'down'."
            
        await page.wait_for_timeout(500)
        await _save_screenshot(page)
        return f"Successfully scrolled page {direction}. Screenshot captured."
    except Exception as e:
        return f"Error scrolling page: {str(e)}"

async def browser_take_screenshot() -> str:
    """Takes a fresh screenshot of the browser viewport."""
    try:
        page = await get_browser_page()
        path = await _save_screenshot(page)
        if path:
            return f"Screenshot successfully saved to {path}."
        return "Error: Playwright screenshot returned empty path."
    except Exception as e:
        return f"Error taking screenshot: {str(e)}"

async def browser_get_html() -> str:
    """Returns a cleaned, token-efficient version of the page HTML source."""
    try:
        page = await get_browser_page()
        raw_html = await page.content()
        
        # Clean HTML to reduce token counts
        soup = BeautifulSoup(raw_html, "html.parser")
        
        # Remove head, scripts, styles, and other metadata
        for script in soup(["script", "style", "noscript", "svg", "iframe", "head"]):
            script.decompose()
            
        # Optional: simplify attributes of elements to keep output small
        for tag in soup.find_all(True):
            # Keep only essential attributes
            essential_attrs = ["id", "class", "href", "placeholder", "name", "value", "type"]
            tag.attrs = {k: v for k, v in tag.attrs.items() if k in essential_attrs}
            
        # Get cleaned HTML snippet
        cleaned_html = soup.prettify()
        # Truncate if too long to prevent context overflow (keep first 6000 chars)
        if len(cleaned_html) > 8000:
            cleaned_html = cleaned_html[:8000] + "\n... [TRUNCATED] ..."
            
        return f"Cleaned Page HTML Source:\n```html\n{cleaned_html}\n```"
    except Exception as e:
        return f"Error retrieving page source: {str(e)}"

import os
import sys
import asyncio
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.browser import browser_navigate, browser_get_html, close_browser

class TestSovereignBrowser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Allow time to launch
        pass
        
    async def asyncTearDown(self):
        await close_browser()
        
    async def test_01_navigation_and_scraping(self):
        print("\n--- Testing Browser Navigation ---")
        
        # Navigate to example.com
        nav_res = await browser_navigate("https://example.com")
        print(nav_res)
        self.assertIn("Successfully navigated", nav_res)
        self.assertIn("Example Domain", nav_res)
        
        # Verify screenshot was saved
        screenshot_path = "~/sovereign_agent/frontend/live_view.png"
        self.assertTrue(os.path.exists(screenshot_path), "Screenshot file was not created!")
        
        # Verify get cleaned html
        html_res = await browser_get_html()
        print("\nCleaned HTML Sample:")
        print(html_res[:500])
        self.assertIn("Example Domain", html_res)
        self.assertIn("<h1>", html_res)
        self.assertNotIn("<script>", html_res)
        self.assertNotIn("<style>", html_res)

if __name__ == "__main__":
    unittest.main()

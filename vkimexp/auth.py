# ------------------------------------------------------------------------------
#  vkimexp [VK dialogs exporter]
#  (c) 2023 A. Shavykin <0.delameter@gmail.com>
# ------------------------------------------------------------------------------

import logging
import random

import browser_cookie3
import yt_dlp

from .common import URL, get_logger, DOMAIN, Context


class Auth:
    def __init__(self, ctx: Context):
        self._cookies = {}
        self._extract_fns: list[callable] = [
            self._extract_ytdlp,
            self._extract_bc3,
        ]
        if ctx.attempt > 1:
            random.shuffle(self._extract_fns)

        while len(self._extract_fns):
            extract_fn = self._extract_fns.pop(0)
            try:
                extract_fn(ctx.browser)
            except Exception as e:
                if ctx.verbose:
                    get_logger().exception(e)
                get_logger().error(f"Cookie extraction failed: {e}")
            else:
                get_logger().info(f"[{ctx.browser}] Extracted {len(self._cookies)} cookies ({extract_fn.__name__})")
                if len(self._cookies) > 0:
                    break

    def _extract_ytdlp(self, browser: str):
        cookiejar = yt_dlp.cookies.extract_cookies_from_browser(browser)
        self._cookies = {c.name: c.value for c in cookiejar.get_cookies_for_url(URL)}

    def _extract_bc3(self, browser: str):
        extractor = getattr(browser_cookie3, browser)
        if not extractor:
            logging.warning(f"Invalid browser: {browser!r}, falling back to default")
            extractor = browser_cookie3.chrome
        cookiejar = extractor(domain_name=DOMAIN)
        self._cookies = {c.name: c.value for c in cookiejar}

    @property
    def cookies(self) -> dict:
        return self._cookies

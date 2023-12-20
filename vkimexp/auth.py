import logging

import yt_dlp

from .common import URL, get_logger


class Auth:
    def __init__(self, browser: str):
        # browser_cookie3 is NOT working with vk.com cookies for some reason :( @TODO
        #
        # import browser_cookie3
        # extractor = getattr(browser_cookie3, browser)
        # if not extractor:
        #     logging.warning(f"Invalid browser: {browser!r}, falling back to default")
        #     extractor = browser_cookie3.chrome
        # cookiejar = extractor(domain_name=DOMAIN)
        # self._cookies = {c.name: c.value for c in cookiejar}

        self._cookies = {}
        try:
            cookiejar = yt_dlp.cookies.extract_cookies_from_browser(browser)
            self._cookies = {c.name: c.value for c in cookiejar.get_cookies_for_url(URL)}
        except Exception as e:
            get_logger().exception(e)
            get_logger().warning("Cookie extraction failed -- cannot proceed")
        else:
            get_logger().info(f"[{browser}] Extracted {len(self._cookies)} cookies")

    @property
    def cookies(self) -> dict:
        return self._cookies

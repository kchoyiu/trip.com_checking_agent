"""Central selector registry. Revalidate against the live page before production use."""
# Includes common upstream/browser security interstitial text. Never bypass these.
CAPTCHA_MARKERS = ("captcha", "verify you are human", "robot check", "access denied", "whaleguard", "anti-bot")
RESULT_SELECTORS = ("[data-testid*='flight']", "[class*='flight-item']", "[class*='FlightCard']")
PRICE_SELECTORS = ("[data-testid*='price']", "[class*='price']", "[class*='Price']")

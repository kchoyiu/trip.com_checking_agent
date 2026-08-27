"""Central selector registry. Revalidate against the live page before production use."""
# Includes common upstream/browser security interstitial text. Never bypass these.
CAPTCHA_MARKERS = ("captcha", "verify you are human", "robot check", "access denied", "whaleguard", "anti-bot")
RESULT_SELECTORS = (
    "[data-testid^='u-flight-card-']",
    "[data-flight-id][data-index]",
    ".result-item.J_FlightItem",
)
PRICE_SELECTORS = (
    "[data-testid='u_price_info']",
    "[data-testid^='flight_price_']",
    "[aria-label*='price' i]",
    "[class*='price']",
    "[class*='Price']",
)
AIRLINE_SELECTORS = (
    "[data-testid*='airline']",
    "[data-testid*='carrier']",
    "[data-testid*='airline-name']",
    "[data-testid='flights-name']",
    "[class*='airline']",
    "[class*='Airline']",
    "[class*='carrier']",
    "[class*='Carrier']",
    "[class*='airline-name']",
    "[class*='carrier-name']",
    ".flights-name",
    ".flight-info-airline__wrap [role='region']",
    "[aria-label*='Airlines' i]",
)

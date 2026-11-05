USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.cricinfo.com",
    "Referer": "https://www.cricinfo.com/",
    "sec-ch-ua": '"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

API_BASE_URL = "https://hs-consumer-api.cricinfo.com/v1/pages/"
PUBLIC_API_BASE_URL = "https://site.web.api.espn.com/apis/site/v2/sports/cricket/"
BASE_URL = "https://www.cricinfo.com"
IMAGE_BASE_URL = "https://p.imgci.com"
INVALID_IMAGES_URL = ["None", None, "", f"{BASE_URL}/", f"{IMAGE_BASE_URL}/"]

HSCI_TOKEN_MAP = {}
EDGE_AUTH_ENCRYPTION_KEY = "9ced54a89687e1173e91c1f225fc02abf275a119fda8a41d731d2b04dac95ff5"
TOKEN_LIFESPAN = 30 * 60

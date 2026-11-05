import requests
from constants import HEADERS

url = "https://hs-consumer-api.espncricinfo.com/v1/pages/series/schedule?seriesId=1510719"
from akamai.edgeauth import EdgeAuth
from constants import EDGE_AUTH_ENCRYPTION_KEY, TOKEN_LIFESPAN
from urllib.parse import urlsplit

def get_token(url_str):
    et = EdgeAuth(
        key=EDGE_AUTH_ENCRYPTION_KEY,
        window_seconds=TOKEN_LIFESPAN,
        escape_early=True
    )
    # FIXED: Avoid double slash
    parsed = urlsplit(url_str)
    query_path = parsed.path
    if parsed.query:
        query_path += "?" + parsed.query
    print("Token generated for path:", query_path)
    return et.generate_url_token(query_path)

headers = HEADERS.copy()
headers["X-Hsci-Auth-Token"] = get_token(url)

resp = requests.get(url, headers=headers)
print("Status Code:", resp.status_code)
if resp.status_code != 200:
    print("Response text:", resp.text[:500])
else:
    print("Success with fixed query_path!")
    print(resp.json().keys())

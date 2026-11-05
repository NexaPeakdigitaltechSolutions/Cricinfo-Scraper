import requests
import re
import json
from constants import HEADERS

url = "https://www.espncricinfo.com/series/ipl-2026-1510719/match-schedule-fixtures-and-results"
resp = requests.get(url, headers=HEADERS)
print("Status Code:", resp.status_code)

if resp.status_code == 200:
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text)
    if match:
        data = json.loads(match.group(1))
        print("Successfully found __NEXT_DATA__")
        try:
            page_props = data['props']['pageProps']
            print("Keys in pageProps:", page_props.keys())
            if 'data' in page_props:
                print("Keys in data:", page_props['data'].keys())
                if 'content' in page_props['data']:
                    print("Keys in content:", page_props['data']['content'].keys())
                    if 'matches' in page_props['data']['content']:
                        matches = page_props['data']['content']['matches']
                        print("Found", len(matches), "matches!")
                        if matches:
                            print("Sample match keys:", matches[0].keys())
        except KeyError as e:
            print("KeyError:", e)
    else:
        print("Could not find __NEXT_DATA__")

import asyncio
import aiohttp
import json
from urllib.parse import urlencode
from akamai.edgeauth import EdgeAuth
from constants import EDGE_AUTH_ENCRYPTION_KEY

async def inspect_var3_comments():
    et = EdgeAuth(key=EDGE_AUTH_ENCRYPTION_KEY, window_seconds=1800, escape_early=True)
    p = {"seriesId": 1539421, "matchId": 1539437, "inningNumber": 1, "commentType": "ALL", "sortDirection": "ASC", "fromInningOver": 1}
    path = f"/v1/pages/match/comments?{urlencode(p)}"
    url = f"https://hs-consumer-api.cricinfo.com{path}"
    token = et.generate_url_token(path)
    
    headers = {
        "accept": "application/json, text/plain, */*",
        "origin": "https://www.cricinfo.com",
        "referer": "https://www.cricinfo.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "x-hsci-auth-token": token,
    }
    
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers) as r:
            data = await r.json()
            comments = data.get("comments", [])
            print(f"Total comments in this batch: {len(comments)}")
            for idx, c in enumerate(comments[:10]):
                print(f"\nDelivery {idx+1} (over {c.get('oversActual')}):")
                for k in ["wagonX", "wagonY", "wagonZone", "pitchLine", "pitchLength", "shotType", "shotControl", "title", "totalRuns"]:
                    print(f"  {k}: {c.get(k)}")

if __name__ == "__main__":
    asyncio.run(inspect_var3_comments())


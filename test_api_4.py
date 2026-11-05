import asyncio
import aiohttp
from apis import AsyncSeriesClient, AsyncCricInfoAuthHandler
import apis
from urllib.parse import urlsplit

class TestAuthHandler(AsyncCricInfoAuthHandler):
    async def get_auth_token(self, url: str) -> str:
        async with self._lock:
            cached_token = self._token_cache.get(url)
            from datetime import datetime, timedelta
            from constants import EDGE_AUTH_ENCRYPTION_KEY, TOKEN_LIFESPAN
            from akamai.edgeauth import EdgeAuth
            
            expiry = datetime.now() + timedelta(seconds=TOKEN_LIFESPAN)
            et = EdgeAuth(
                key=EDGE_AUTH_ENCRYPTION_KEY,
                window_seconds=TOKEN_LIFESPAN,
                escape_early=True,
            )
            # Try generate_acl_token
            token = et.generate_acl_token("/*")
            self._token_cache[url] = {"token": token, "expiry": expiry}
            return token

class TestSeriesClient(AsyncSeriesClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_handler = TestAuthHandler()

async def test_api():
    async with aiohttp.ClientSession() as session:
        client = TestSeriesClient(session=session)
        try:
            res = await client.get_schedule(id=1510719)
            print("SUCCESS ACL")
            return
        except Exception as e:
            print(f"FAILED ACL: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())

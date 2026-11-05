import asyncio
import aiohttp
from apis import AsyncSeriesClient

async def test_api():
    async with aiohttp.ClientSession() as session:
        client = AsyncSeriesClient(session=session)
        try:
            res = await client.get_schedule(id=1510719)
            print("SUCCESS")
            print(res)
        except Exception as e:
            print(f"FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())

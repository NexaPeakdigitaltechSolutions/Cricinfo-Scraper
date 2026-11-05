import asyncio
import aiohttp
from apis import AsyncMatchClient

async def check():
    async with aiohttp.ClientSession() as session:
        client = AsyncMatchClient(session=session)
        res = await client.get_ball_commentary(id=1527674, series_id=1510719, inning_number=1, from_over=1)
        comments = res.get('comments', [])
        for c in comments:
            ext = (c.get('wides') or 0) + (c.get('noballs') or 0) + (c.get('byes') or 0) + (c.get('legbyes') or 0)
            if ext > 0:
                print(f"id={c.get('id')} overNum={c.get('overNumber')} ballNum={c.get('ballNumber')} oversActual={c.get('oversActual')} oversUnique={c.get('oversUnique')} wides={c.get('wides')} nb={c.get('noballs')} lb={c.get('legbyes')}")

if __name__ == "__main__":
    asyncio.run(check())

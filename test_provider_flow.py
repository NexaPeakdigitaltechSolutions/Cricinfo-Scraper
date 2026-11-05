import asyncio
from apis import (
    AsyncSeriesClient,
    AsyncESPNPublicSiteClient,
    CricketDataProvider,
    AccessDeniedError,
)

async def test_providers():
    print("=== Test 1: ESPNcricinfo direct (expecting 403 AccessDeniedError) ===")
    client = AsyncSeriesClient()
    try:
        await client.get_schedule(1510719)
        print("UNEXPECTED: Succeeded")
    except AccessDeniedError as e:
        print(f"SUCCESS: Caught expected AccessDeniedError: {e}")
    except Exception as e:
        print(f"OTHER ERROR: {type(e).__name__}: {e}")
    finally:
        await client.close()

    print("\n=== Test 2: ESPN Public Site API direct (expecting 200 OK with matches) ===")
    pub_client = AsyncESPNPublicSiteClient()
    try:
        res = await pub_client.get_schedule(1510719)
        matches = res.get("content", {}).get("matches", [])
        print(f"SUCCESS: Retrieved {len(matches)} matches!")
        if matches:
            print("Sample match:", matches[0].get("title"))
    except Exception as e:
        print(f"FAILED: {e}")
    finally:
        await pub_client.close()

    print("\n=== Test 3: CricketDataProvider Auto Mode (expecting seamless fallback) ===")
    provider = CricketDataProvider()
    try:
        res = await provider.get_schedule(1510719, provider_mode="auto")
        matches = res.get("content", {}).get("matches", [])
        pu = res.get("provider_used")
        print(f"SUCCESS: Auto provider returned {len(matches)} matches via [{pu}]!")
    except Exception as e:
        print(f"FAILED: {e}")
    finally:
        await provider.close()

if __name__ == "__main__":
    asyncio.run(test_providers())


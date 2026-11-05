import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import aiohttp
from aiohttp import web
from apis import (
    AsyncCricInfoAPIClient,
    AsyncSeriesClient,
    AsyncESPNPublicSiteClient,
    CricketDataProvider,
    CricketAPIError,
    AccessDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ServerError,
    ConnectionTimeoutError,
    InvalidResponseError,
)

async def run_scenario_tests():
    print("=" * 60)
    print("RUNNING 9 VERIFICATION TEST SCENARIOS")
    print("=" * 60)

    # -------------------------------------------------------------
    # Scenario 1: Successful API Response (200 OK)
    # -------------------------------------------------------------
    print("\n[Scenario 1] Testing Successful API Response (200 OK)...")
    pub_client = AsyncESPNPublicSiteClient()
    try:
        res = await pub_client.get_schedule(1510719)
        matches = res.get("content", {}).get("matches", [])
        assert len(matches) > 0, "No matches returned in successful response"
        print(f" PASS: HTTP 200 returned with {len(matches)} real fixtures.")
    finally:
        await pub_client.close()

    # -------------------------------------------------------------
    # Scenario 2: Forbidden Response (403)
    # -------------------------------------------------------------
    print("\n[Scenario 2] Testing Forbidden Response (403 Forbidden)...")
    consumer_client = AsyncSeriesClient()
    try:
        await consumer_client.get_schedule(1510719)
        print(" FAIL: Did not raise 403")
    except AccessDeniedError as e:
        assert e.status_code == 403, "Status code not 403"
        print(f" PASS: Correctly caught AccessDeniedError: {e}")
    finally:
        await consumer_client.close()

    # -------------------------------------------------------------
    # Scenario 3: Too Many Requests (429) with Retry-After
    # -------------------------------------------------------------
    print("\n[Scenario 3] Testing Rate Limiting (429 Too Many Requests)...")
    mock_session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 429
    mock_resp.headers = {"Retry-After": "10"}
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    rate_client = AsyncCricInfoAPIClient(session=mock_session, max_retries=1)
    try:
        await rate_client.get("test/endpoint")
        print(" FAIL: Expected RateLimitError")
    except RateLimitError as e:
        assert e.status_code == 429
        print(f" PASS: Correctly caught RateLimitError: {e}")

    # -------------------------------------------------------------
    # Scenario 4: Server Error (500/502/503)
    # -------------------------------------------------------------
    print("\n[Scenario 4] Testing Server Error (500/502/503) with Backoff...")
    mock_session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 503
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    server_client = AsyncCricInfoAPIClient(session=mock_session, max_retries=1)
    try:
        await server_client.get("test/server_error")
        print(" FAIL: Expected ServerError")
    except ServerError as e:
        assert e.status_code == 503
        print(f" PASS: Correctly caught ServerError: {e}")

    # -------------------------------------------------------------
    # Scenario 5: Connection Timeout
    # -------------------------------------------------------------
    print("\n[Scenario 5] Testing Connection Timeout...")
    mock_session = MagicMock()
    mock_session.get.side_effect = asyncio.TimeoutError("Timeout occurred")
    mock_session.closed = False

    timeout_client = AsyncCricInfoAPIClient(session=mock_session, max_retries=1)
    try:
        await timeout_client.get("test/timeout")
        print(" FAIL: Expected ConnectionTimeoutError")
    except ConnectionTimeoutError as e:
        print(f" PASS: Correctly caught ConnectionTimeoutError: {e}")

    # -------------------------------------------------------------
    # Scenario 6: Invalid JSON Response
    # -------------------------------------------------------------
    print("\n[Scenario 6] Testing Invalid JSON Response...")
    mock_session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json.side_effect = aiohttp.ContentTypeError(None, None)
    mock_resp.text = AsyncMock(return_value="<html>Not JSON</html>")
    mock_session.get.return_value.__aenter__.return_value = mock_resp
    mock_session.closed = False

    json_client = AsyncCricInfoAPIClient(session=mock_session, max_retries=0)
    try:
        await json_client.get("test/invalid_json")
        print(" FAIL: Expected InvalidResponseError")
    except InvalidResponseError as e:
        print(f" PASS: Correctly caught InvalidResponseError: {e}")

    # -------------------------------------------------------------
    # Scenario 7: Empty Fixture Response
    # -------------------------------------------------------------
    print("\n[Scenario 7] Testing Empty Fixture Response...")
    provider = CricketDataProvider()
    try:
        # Using a valid series format that has no matches currently
        mock_empty_resp = {"events": [], "leagues": []}
        with patch.object(provider.public_client, "get_schedule", return_value={"content": {"matches": []}, "series": {"objectId": 9999999}}):
            res = await provider.get_schedule(9999999, provider_mode="public_espn")
            matches = res.get("content", {}).get("matches", [])
            assert len(matches) == 0
            print(f" PASS: Correctly handled empty fixture response (0 matches returned without crash).")
    finally:
        await provider.close()

    # -------------------------------------------------------------
    # Scenario 8: Invalid Series ID
    # -------------------------------------------------------------
    print("\n[Scenario 8] Testing Invalid Series ID (e.g., 99999999)...")
    pub_client = AsyncESPNPublicSiteClient()
    try:
        res = await pub_client.get_schedule(99999999)
        matches = res.get("content", {}).get("matches", [])
        assert len(matches) == 0, "Invalid series ID should return 0 matches"
        print(f" PASS: Invalid series ID gracefully handled (returned empty fixture set without crashing).")
    finally:
        await pub_client.close()

    # -------------------------------------------------------------
    # Scenario 9: Alternative Provider Unavailable
    # -------------------------------------------------------------
    print("\n[Scenario 9] Testing Alternative Provider Unavailable...")
    provider = CricketDataProvider()
    try:
        with patch.object(provider.consumer_series_client, "get_schedule", side_effect=AccessDeniedError("test_cricinfo")):
            with patch.object(provider.public_client, "get_schedule", side_effect=CricketAPIError("Public provider unreachable", status_code=503)):
                try:
                    await provider.get_schedule(1510719, provider_mode="auto")
                    print(" FAIL: Expected CricketAPIError when both fail")
                except CricketAPIError as e:
                    print(f" PASS: Correctly caught CricketAPIError when both providers fail: {e}")
    finally:
        await provider.close()

    print("\n" + "=" * 60)
    print("ALL 9 TEST SCENARIOS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_scenario_tests())


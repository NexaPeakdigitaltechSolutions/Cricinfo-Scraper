import asyncio
import json
import logging
import re
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit

import aiohttp
from akamai.edgeauth import EdgeAuth

from constants import (
    API_BASE_URL,
    EDGE_AUTH_ENCRYPTION_KEY,
    HEADERS,
    PUBLIC_API_BASE_URL,
    TOKEN_LIFESPAN,
    USER_AGENT,
)

logger = logging.getLogger("cricket_api")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Names reported back to the UI so it can say exactly where ball data came from.
PROVIDER_CRICINFO = "ESPNcricinfo Consumer API"
PROVIDER_PUBLIC = "ESPN Public Site API"
PROVIDER_PUBLIC_FALLBACK = "ESPN Public Site API (Fallback)"
PROVIDER_NONE = "No data"

# Retry policy applied to each Cricinfo commentary page before any fallback is considered.
CONSUMER_PAGE_ATTEMPTS = 3
CONSUMER_RETRY_BACKOFF = (2.0, 5.0)


# =====================================================================
# Custom Exceptions for Granular and Safe Error Handling
# =====================================================================

class CricketAPIError(Exception):
    """Base exception for Cricket API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, endpoint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.endpoint = endpoint

    def __str__(self) -> str:
        code_str = f" [HTTP {self.status_code}]" if self.status_code else ""
        ep_str = f" on {self.endpoint}" if self.endpoint else ""
        return f"{self.message}{code_str}{ep_str}"


class AccessDeniedError(CricketAPIError):
    """Raised on HTTP 403 Forbidden. Should not be retried."""
    def __init__(self, endpoint: str, details: str = "Access Denied by CDN/WAF security controls"):
        super().__init__(details, status_code=403, endpoint=endpoint)


class RateLimitError(CricketAPIError):
    """Raised on HTTP 429 Too Many Requests."""
    def __init__(self, endpoint: str, retry_after: Optional[float] = None):
        super().__init__("Rate limit exceeded", status_code=429, endpoint=endpoint)
        self.retry_after = retry_after


class ResourceNotFoundError(CricketAPIError):
    """Raised on HTTP 404 Not Found."""
    def __init__(self, endpoint: str):
        super().__init__("Resource not found", status_code=404, endpoint=endpoint)


class ServerError(CricketAPIError):
    """Raised on HTTP 5xx Server Errors."""
    def __init__(self, endpoint: str, status_code: int, details: str = "Internal server error"):
        super().__init__(details, status_code=status_code, endpoint=endpoint)


class ConnectionTimeoutError(CricketAPIError):
    """Raised when an API request times out."""
    def __init__(self, endpoint: str, timeout_seconds: float):
        super().__init__(f"Request timed out after {timeout_seconds}s", endpoint=endpoint)
        self.timeout_seconds = timeout_seconds


class InvalidResponseError(CricketAPIError):
    """Raised when the server returns invalid or non-JSON content."""
    def __init__(self, endpoint: str, snippet: str):
        super().__init__(f"Invalid response payload: {snippet}", endpoint=endpoint)


# =====================================================================
# Safe Logging Helper (Never leak tokens, secrets, or cookies)
# =====================================================================

def sanitize_url(url: str) -> str:
    """Strip or redact sensitive query parameters from logged URLs."""
    return re.sub(r"(token|auth|key|secret)=[^&]+", r"\1=[REDACTED]", url, flags=re.IGNORECASE)


# =====================================================================
# Async CricInfo Auth Handler (Akamai EdgeAuth)
# =====================================================================

class AsyncCricInfoAuthHandler:
    """
    Async-ready Auth Handler for ESPNcricinfo consumer APIs.
    Generates HMAC-signed tokens using Akamai EdgeAuth.
    """

    def __init__(self) -> None:
        self._token_cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._buffer_time = timedelta(seconds=10)

    def _is_token_expired(self, expiry: datetime) -> bool:
        return expiry <= datetime.now() + self._buffer_time

    def clear_cache(self) -> None:
        """Clear cached tokens (useful when a token is invalidated)."""
        self._token_cache.clear()

    async def get_auth_token(self, url: str) -> str:
        """Async retrieval of auth token with caching and lock protection."""
        async with self._lock:
            query_path = urlsplit(url)._replace(scheme="", netloc="").geturl()
            cached_token = self._token_cache.get(url)

            if cached_token is None or self._is_token_expired(cached_token["expiry"]):
                expiry = datetime.now() + timedelta(seconds=TOKEN_LIFESPAN)
                et = EdgeAuth(
                    key=EDGE_AUTH_ENCRYPTION_KEY,
                    window_seconds=TOKEN_LIFESPAN,
                    escape_early=True,
                )
                token = et.generate_url_token(query_path)
                self._token_cache[url] = {"token": token, "expiry": expiry}
                return token

            return cached_token["token"]


# =====================================================================
# Base Async HTTP Client with Connection Pooling & Retry Policy
# =====================================================================

class AsyncCricInfoAPIClient:
    """
    Asynchronous base client for ESPNcricinfo Consumer API with
    exponential backoff, granular timeout control, and 403 prevention.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = API_BASE_URL,
        timeout_seconds: float = 12.0,
        max_retries: int = 2,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self.base_url = base_url
        self.headers = HEADERS.copy()
        self.auth_handler = AsyncCricInfoAuthHandler()
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=5.0)
        self.max_retries = max_retries

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def _prepare_query(self, query: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return query.copy() if query else {}

    def _get_full_url(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> str:
        if self.base_url is None or endpoint.startswith(("http://", "https://")):
            url = endpoint
        else:
            url = self.base_url.rstrip("/") + "/" + endpoint.lstrip("/")

        if params:
            query_string = urlencode(params)
            url += f"?{query_string}"
        return url

    async def get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Execute an asynchronous GET request with HMAC token injection,
        safe error classification, and controlled exponential backoff.
        """
        full_url = self._get_full_url(endpoint, params)
        safe_url = sanitize_url(full_url)
        session = await self.get_session()

        # Generate auth token
        try:
            token = await self.auth_handler.get_auth_token(full_url)
        except Exception as e:
            logger.warning(f"Could not generate EdgeAuth token for {safe_url}: {e}")
            token = None

        req_headers = self.headers.copy()
        if token:
            req_headers["X-Hsci-Auth-Token"] = token

        attempt = 0
        backoff = 1.0

        while attempt <= self.max_retries:
            attempt += 1
            try:
                async with session.get(full_url, headers=req_headers, **kwargs) as response:
                    status = response.status

                    # Success (200 OK)
                    if status == 200:
                        try:
                            return await response.json()
                        except (aiohttp.ContentTypeError, json.JSONDecodeError):
                            raw_text = (await response.text())[:200]
                            raise InvalidResponseError(endpoint=safe_url, snippet=raw_text)

                    # 404 Not Found
                    if status == 404:
                        return {}

                    # 403 Forbidden - Do NOT retry, fail fast with diagnostic info
                    if status == 403:
                        logger.error(
                            f"HTTP 403 Forbidden on endpoint: {safe_url} "
                            "(Access restricted by Akamai EdgeAuth / WAF security policies)"
                        )
                        raise AccessDeniedError(endpoint=safe_url)

                    # 429 Too Many Requests - Respect Retry-After if reasonable
                    if status == 429:
                        retry_after_hdr = response.headers.get("Retry-After")
                        delay = float(retry_after_hdr) if retry_after_hdr and retry_after_hdr.isdigit() else backoff
                        if attempt <= self.max_retries and delay <= 6.0:
                            logger.warning(f"Rate limited (429) on {safe_url}. Backing off for {delay:.1f}s...")
                            await asyncio.sleep(delay)
                            backoff *= 2.0
                            continue
                        raise RateLimitError(endpoint=safe_url, retry_after=delay)

                    # 5xx Server Errors - Eligible for retry
                    if 500 <= status < 600:
                        if attempt <= self.max_retries:
                            logger.warning(
                                f"Server error {status} on {safe_url}. Retrying ({attempt}/{self.max_retries}) in {backoff}s..."
                            )
                            await asyncio.sleep(backoff)
                            backoff *= 2.0
                            continue
                        raise ServerError(endpoint=safe_url, status_code=status)

                    # Other 4xx client errors
                    response.raise_for_status()
                    return await response.json()

            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as net_err:
                if attempt <= self.max_retries:
                    logger.warning(
                        f"Network/Timeout error on {safe_url} ({net_err}). Retrying ({attempt}/{self.max_retries}) in {backoff}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue
                if isinstance(net_err, asyncio.TimeoutError):
                    raise ConnectionTimeoutError(endpoint=safe_url, timeout_seconds=self.timeout.total or 12.0)
                raise CricketAPIError(f"Connection failed: {net_err}", endpoint=safe_url)
            except (AccessDeniedError, RateLimitError, ResourceNotFoundError, ServerError, InvalidResponseError):
                raise
            except Exception as e:
                logger.error(f"Unexpected error querying {safe_url}: {e}")
                raise CricketAPIError(f"Unexpected error: {e}", endpoint=safe_url)

        raise CricketAPIError(f"Request failed after {self.max_retries} retries", endpoint=safe_url)


# =====================================================================
# ESPN Cricinfo Consumer Clients (Preserving Existing Architecture)
# =====================================================================

class AsyncMatchClient(AsyncCricInfoAPIClient):
    """Client for individual matches on ESPNcricinfo."""

    def _get_query_with_ids(
        self, id: int, series_id: int = 0, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        q = self._prepare_query(query)
        q["matchId"] = id
        if series_id:
            q["seriesId"] = series_id
        return q

    async def get_one(
        self, id: int, series_id: int = 0, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._get_query_with_ids(id, series_id, query)
        return await self.get("match/home", params)

    async def get_commentary(
        self, id: int, series_id: int = 0, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._get_query_with_ids(id, series_id, query)
        return await self.get("match/commentary", params)

    async def get_scorecard(
        self, id: int, series_id: int = 0, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._get_query_with_ids(id, series_id, query)
        return await self.get("match/scorecard", params)

    async def get_ball_commentary(
        self,
        id: int,
        series_id: int = 0,
        inning_number: int = 1,
        from_over: Optional[int] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params = self._get_query_with_ids(id, series_id, query)
        params.update(
            {
                "inningNumber": inning_number,
                "commentType": params.get("commentType", "ALL"),
                "sortDirection": params.get("sortDirection", "ASC"),
            }
        )
        if from_over is not None:
            params["fromInningOver"] = from_over
        return await self.get("match/comments", params)

    async def list(self, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = self._prepare_query(query)
        return await self.get("matches/result", params)


class AsyncSeriesClient(AsyncCricInfoAPIClient):
    """Client for series/tournament data on ESPNcricinfo."""

    async def get_one(
        self, id: int, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._prepare_query(query)
        params["seriesId"] = id
        return await self.get("series/home", params)

    async def get_standings(
        self, id: int, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._prepare_query(query)
        params["seriesId"] = id
        return await self.get("series/standings", params)

    async def get_schedule(
        self, id: int, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._prepare_query(query)
        params["seriesId"] = id
        return await self.get("series/schedule", params)


class AsyncPlayerClient(AsyncCricInfoAPIClient):
    async def get_one(
        self, id: int, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._prepare_query(query)
        params["playerId"] = id
        return await self.get("player/home", params)


class AsyncTeamClient(AsyncCricInfoAPIClient):
    async def get_one(
        self, id: int, query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        params = self._prepare_query(query)
        params["teamId"] = id
        return await self.get("team/home", params)


# =====================================================================
# Alternative Legitimate Data Provider: ESPN Official Public Site API
# =====================================================================

class AsyncESPNPublicSiteClient:
    """
    Legitimate public API client accessing ESPN's open site service
    (site.web.api.espn.com), which provides live scoreboards and schedules
    without requiring Akamai EdgeAuth tokens.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
        base_url: str = PUBLIC_API_BASE_URL,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self.base_url = base_url
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=5.0)

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def get_schedule(self, series_id: int) -> Dict[str, Any]:
        """
        Fetch series schedule from ESPN Public Site API and normalize
        it into the standard ESPNcricinfo schema expected by the application.
        """
        session = await self.get_session()
        # Query full season schedule or current scoreboard
        current_year = datetime.now().year
        urls_to_try = [
            f"{self.base_url.rstrip('/')}/{series_id}/scoreboard?dates={current_year}",
            f"{self.base_url.rstrip('/')}/{series_id}/scoreboard?season={current_year}",
            f"{self.base_url.rstrip('/')}/{series_id}/scoreboard",
        ]

        data = None
        last_error = None
        for target_url in urls_to_try:
            try:
                async with session.get(target_url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("events"):
                            break
                    elif resp.status == 404:
                        continue
                    else:
                        resp.raise_for_status()
            except Exception as e:
                last_error = e
                continue

        if not data or not data.get("events"):
            if last_error:
                raise CricketAPIError(f"Public API error: {last_error}", endpoint=str(series_id))
            return {"content": {"matches": []}, "series": {"objectId": series_id}}

        # Normalize into ESPNcricinfo compatible match format
        events = data.get("events", [])
        league = data.get("leagues", [{}])[0] if data.get("leagues") else {}
        normalized_matches = []

        for ev in events:
            comp = ev.get("competitions", [{}])[0] if ev.get("competitions") else {}
            status_obj = comp.get("status", {})
            status_type = status_obj.get("type", {})
            state_str = status_type.get("state", "").lower()

            if status_type.get("completed") or state_str == "post" or status_type.get("name") == "STATUS_FINAL":
                status = "RESULT"
            elif state_str == "in" or status_type.get("name") == "STATUS_IN_PROGRESS":
                status = "LIVE"
            else:
                status = "UPCOMING"

            competitors = comp.get("competitors", [])
            teams_payload = []
            for c in competitors:
                t = c.get("team", {})
                teams_payload.append({
                    "team": {
                        "id": int(t.get("id", 0)) if str(t.get("id", "0")).isdigit() else 0,
                        "name": t.get("name", "TBD"),
                        "longName": t.get("displayName") or t.get("name", "TBD"),
                        "abbreviation": t.get("abbreviation") or t.get("shortDisplayName") or t.get("name", "TBD"),
                        "imageUrl": t.get("logo"),
                    },
                    "score": c.get("score"),
                    "isHome": c.get("homeAway") == "home",
                })

            venue_info = comp.get("venue", {})
            venue_name = venue_info.get("fullName") or venue_info.get("address", {}).get("city", "Unknown Venue")

            outcome = status_obj.get("summary") or status_type.get("shortDetail") or status_type.get("detail", "")
            title = comp.get("description") or ev.get("name", "Match")

            match_id = int(ev.get("id", 0)) if str(ev.get("id", "0")).isdigit() else ev.get("id")

            normalized_matches.append({
                "objectId": match_id,
                "id": match_id,
                "title": title,
                "status": status,
                "statusText": outcome,
                "startDate": ev.get("date"),
                "ground": {"name": venue_name},
                "teams": teams_payload,
                "provider": "ESPN_PUBLIC_SITE",
            })

        return {
            "series": {
                "objectId": series_id,
                "name": league.get("name", "Cricket Series"),
                "slug": league.get("slug", "cricket-series"),
            },
            "content": {"matches": normalized_matches},
        }

    async def get_match_summary(self, series_id: int, match_id: int) -> Dict[str, Any]:
        """Fetch match details, rosters, and status from the public summary endpoint."""
        session = await self.get_session()
        url = f"{self.base_url.rstrip('/')}/{series_id}/summary?event={match_id}"
        async with session.get(url, headers=self.headers) as resp:
            if resp.status == 200:
                return await resp.json()
            if resp.status == 404:
                return {}
            resp.raise_for_status()
            return await resp.json()

    async def get_play_by_play(
        self, series_id: int, match_id: int, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Fetch ball-by-ball commentary and deliveries from the authorized ESPN Public Site API.
        Normalizes every delivery to match Cricinfo commentary delivery structure.
        """
        session = await self.get_session()
        url = f"{self.base_url.rstrip('/')}/{series_id}/playbyplay?event={match_id}&limit={limit}"
        try:
            async with session.get(url, headers=self.headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Public playbyplay returned HTTP {resp.status} for match {match_id}")
                    return []
                data = await resp.json()
        except Exception as e:
            logger.warning(f"Error requesting playbyplay for match {match_id}: {e}")
            return []

        items = (data.get("commentary") or {}).get("items") or []
        if not items:
            return []

        deliveries = []
        for item in items:
            over_obj = item.get("over") or {}
            inn_obj = item.get("innings") or {}
            bat_obj = item.get("batsman") or {}
            bowl_obj = item.get("bowler") or {}
            other_bat_obj = item.get("otherBatsman") or {}
            dism_obj = item.get("dismissal") or {}
            team_obj = item.get("team") or {}
            play_type = item.get("playType") or {}
            pt_desc = str(play_type.get("description", "")).lower()
            st_text = str(item.get("shortText", "")).lower()
            score_val = item.get("scoreValue", 0)

            is_wicket = bool(dism_obj.get("dismissal", False))
            is_four = bool("four" in pt_desc or "four" in st_text)
            is_six = bool("six" in pt_desc or "six" in st_text)
            is_wide = bool("wide" in pt_desc or "wide" in st_text)
            is_legbye = bool("leg bye" in pt_desc or "leg bye" in st_text)
            is_bye = bool("bye" in pt_desc and not is_legbye)
            is_noball = bool("no ball" in pt_desc or "no ball" in st_text)

            if is_wide or is_legbye or is_bye:
                bat_runs = 0
            elif is_noball:
                bat_runs = max(0, score_val - 1)
            else:
                bat_runs = score_val

            wides_count = 1 if is_wide else over_obj.get("wide", 0)
            noballs_count = 1 if is_noball else over_obj.get("noBall", 0)
            byes_count = score_val if is_bye else over_obj.get("byes", 0)
            legbyes_count = score_val if is_legbye else over_obj.get("legByes", 0)

            ball_id = int(item["id"]) if str(item.get("id", "")).isdigit() else item.get("sequence", 0)
            is_over_complete = bool(over_obj.get("complete", False))

            # Commentary NLP shot detection
            comm_text = str(item.get("text") or "")
            short_t = str(item.get("shortText") or "")
            comb_text = f"{short_t} {comm_text}".lower()
            detected_shot = None
            for p_pattern, p_label in [
                (r"\b(cover drive|straight drive|on drive|off drive|square drive|drive[s]?)\b", "Drive"),
                (r"\b(pull[s]?|pull shot)\b", "Pull"),
                (r"\b(hook[s]?)\b", "Hook"),
                (r"\b(flick[s]?|flicked)\b", "Flick"),
                (r"\b(leg glance|glance[s]?)\b", "Glance"),
                (r"\b(cut[s]?|square cut|late cut|upper cut|uppercut)\b", "Cut"),
                (r"\b(reverse sweep|paddle sweep|slog sweep|sweep[s]?)\b", "Sweep"),
                (r"\b(lofted drive|lofted shot|loft[s]?|lofts|lofted)\b", "Lofted Shot"),
                (r"\b(ramp|ramp shot|scoop|dilscoop)\b", "Ramp / Scoop"),
                (r"\b(defend[s]?|defended|defensive|block[s]?|blocked)\b", "Defensive"),
                (r"\b(edge[s]?|edged|thick edge|outside edge|inside edge|top edge)\b", "Edge"),
            ]:
                if re.search(p_pattern, comb_text, re.IGNORECASE):
                    detected_shot = p_label
                    break

            # Full Cricinfo commentary delivery schema parity (matching data_1539421.csv)
            rec = {
                "_uid": ball_id,
                "id": ball_id,
                "ballsActual": None,
                "oversUnique": over_obj.get("unique"),
                "oversActual": over_obj.get("actual"),
                "overNumber": over_obj.get("number"),
                "inningNumber": item.get("period") or inn_obj.get("number", 1),
                "ballsUnique": None,
                "ballNumber": over_obj.get("ball"),
                "totalRuns": score_val,
                "batsmanRuns": bat_runs,
                "isFour": is_four,
                "isSix": is_six,
                "isWicket": is_wicket,
                "dismissalType": dism_obj.get("type") if is_wicket else None,
                "byes": byes_count,
                "legbyes": legbyes_count,
                "wides": wides_count,
                "noballs": noballs_count,
                "penalties": 0,
                "wagonX": None,
                "wagonY": None,
                "wagonZone": None,
                "pitchLine": None,
                "pitchLength": None,
                "shotType": None,
                "shotControl": None,
                "shotType_derived": detected_shot,
                "shotType_source": "Derived (Commentary NLP)" if detected_shot else None,
                "timestamp": item.get("date"),
                "modified": item.get("date"),
                "batsmanPlayerId": bat_obj.get("athlete", {}).get("id"),
                "nonStrikerPlayerId": other_bat_obj.get("athlete", {}).get("id"),
                "bowlerPlayerId": bowl_obj.get("athlete", {}).get("id"),
                "outPlayerId": dism_obj.get("batsman", {}).get("athlete", {}).get("id") if is_wicket else None,
                "totalInningRuns": inn_obj.get("runs", 0),
                "totalInningWickets": inn_obj.get("wickets", 0),
                "title": item.get("shortText") or "",
                "dismissalText": None,
                "commentPreTextItems": [{"type": "HTML", "html": item["preText"]}] if item.get("preText") else None,
                "commentTextItems": [{"type": "HTML", "html": item["text"]}] if item.get("text") else None,
                "commentPostTextItems": [{"type": "HTML", "html": item["postText"]}] if item.get("postText") else None,
                "commentVideos": [],
                "commentImages": [],
                "events": [],
                "over": None,
                "batsmanStatText": None,
                "bowlerStatText": None,
                "smartStats": [],
                "short": dism_obj.get("type") if is_wicket else None,
                "long": dism_obj.get("text") if is_wicket else None,
                "commentary": dism_obj.get("text") if is_wicket else None,
                "fielderText": dism_obj.get("fielderText") if is_wicket else None,
                "bowlerText": dism_obj.get("bowlerText") if is_wicket else None,
                "id_1": match_id,
                "inning": item.get("period") or inn_obj.get("number", 1),
                "id_2": team_obj.get("id") if is_over_complete else None,
                "objectId": team_obj.get("id") if is_over_complete else None,
                "scribeId": team_obj.get("id") if is_over_complete else None,
                "slug": team_obj.get("name", "").lower().replace(" ", "-") if is_over_complete else None,
                "name": team_obj.get("name") if is_over_complete else None,
                "longName": team_obj.get("displayName") if is_over_complete else None,
                "abbreviation": team_obj.get("abbreviation") if is_over_complete else None,
                "unofficialName": None,
                "imageUrl": None,
                "isCountry": True if is_over_complete else None,
                "primaryColor": "#b7c0c8" if is_over_complete else None,
                "id_3": None,
                "objectId_1": None,
                "slug_1": None,
                "url": None,
                "width": None,
                "height": None,
                "caption": None,
                "longCaption": None,
                "credit": None,
                "photographer": None,
                "peerUrls": None,
                "id_4": team_obj.get("id") if is_over_complete else None,
                "objectId_2": team_obj.get("id") if is_over_complete else None,
                "name_1": team_obj.get("name") if is_over_complete else None,
                "shortName": team_obj.get("name") if is_over_complete else None,
                "abbreviation_1": team_obj.get("abbreviation") if is_over_complete else None,
                "slug_2": team_obj.get("name", "").lower().replace(" ", "-") if is_over_complete else None,
                "inningNumber_1": item.get("period") if is_over_complete else None,
                "overNumber_1": over_obj.get("number") if is_over_complete else None,
                "overRuns": over_obj.get("runs") if is_over_complete else None,
                "overWickets": over_obj.get("wickets") if is_over_complete else None,
                "totalRuns_1": over_obj.get("runs") if is_over_complete else None,
                "totalWickets": inn_obj.get("wickets") if is_over_complete else None,
                "target": inn_obj.get("target", 0) if is_over_complete else None,
                "overLimit": over_obj.get("limit", 20.0) if is_over_complete else None,
                "ballLimit": inn_obj.get("ballLimit", 120.0) if is_over_complete else None,
                "isMaiden": bool(over_obj.get("maiden", 0) > 0) if is_over_complete else None,
                "isComplete": is_over_complete if is_over_complete else None,
                "isSuperOver": False if is_over_complete else None,
                "overEndBatsmen": None,
                "overEndBowlers": None,
                "rollover": None,
                "score": item.get("homeScore") or inn_obj.get("runs"),
                "winProbability": None,
                # Enriched metadata
                "batsman_name": bat_obj.get("athlete", {}).get("displayName"),
                "bowler_name": bowl_obj.get("athlete", {}).get("displayName"),
                "non_striker_name": other_bat_obj.get("athlete", {}).get("displayName"),
                "commentary_clean": re.sub(r"<[^>]+>", "", item.get("text", "")).strip(),
                "match_context.id": match_id,
                "match_context.series_id": series_id,
                "match_context.inning": item.get("period") or inn_obj.get("number", 1),
                "data_source": PROVIDER_PUBLIC,
            }
            deliveries.append(rec)
        return deliveries


# =====================================================================
# Modular Data Provider Manager (Seamless Fallback)
# =====================================================================

class CricketDataProvider:
    """
    Unified manager that routes requests to ESPNcricinfo Consumer API
    and automatically falls back to ESPN Public Site API if 403 Forbidden is met.
    """

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self.consumer_series_client = AsyncSeriesClient(session=session)
        self.consumer_match_client = AsyncMatchClient(session=session)
        self.public_client = AsyncESPNPublicSiteClient(session=session)

    async def get_schedule(
        self, series_id: int, provider_mode: str = "auto"
    ) -> Dict[str, Any]:
        """
        Retrieve series schedule with provider mode selection.
        Modes: 'auto', 'espncricinfo', 'public_espn'
        """
        # Mode 1: Public ESPN directly
        if provider_mode == "public_espn":
            res = await self.public_client.get_schedule(series_id)
            res["provider_used"] = "ESPN Public Site API"
            return res

        # Mode 2: ESPNcricinfo strictly
        if provider_mode == "espncricinfo":
            res = await self.consumer_series_client.get_schedule(series_id)
            res["provider_used"] = "ESPNcricinfo Consumer API"
            return res

        # Mode 3: AUTO (ESPNcricinfo with automatic Public ESPN fallback)
        try:
            logger.info(f"Querying ESPNcricinfo Consumer API for series {series_id}...")
            res = await self.consumer_series_client.get_schedule(series_id)
            if res and res.get("content", {}).get("matches"):
                res["provider_used"] = "ESPNcricinfo Consumer API"
                return res
        except AccessDeniedError as e:
            logger.warning(
                f"ESPNcricinfo returned HTTP 403 Forbidden ({e.message}). "
                "Seamlessly activating authorized fallback provider (ESPN Public Site API)..."
            )
        except Exception as e:
            logger.warning(f"ESPNcricinfo request failed ({e}). Falling back to ESPN Public Site API...")

        # Fallback execution
        res = await self.public_client.get_schedule(series_id)
        res["provider_used"] = "ESPN Public Site API (Fallback)"
        return res

    # ------------------------------------------------------------------
    # Ball-by-ball commentary: retries first, explicit fallback, provenance
    # ------------------------------------------------------------------
    async def _get_consumer_page(
        self, series_id: int, match_id: int, inning: int, from_over: int
    ) -> Dict[str, Any]:
        """
        Fetch one page of Cricinfo commentary.

        Transient failures (429, 5xx, timeouts, bad JSON) are retried with backoff.
        A 403 is retried once, because Akamai also answers 403 to short request bursts.
        If every attempt fails the last error is raised so the caller can decide
        whether to fall back.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, CONSUMER_PAGE_ATTEMPTS + 1):
            try:
                return await self.consumer_match_client.get_ball_commentary(
                    id=match_id, series_id=series_id, inning_number=inning, from_over=from_over
                )
            except CricketAPIError as e:
                last_exc = e
                max_attempts = 2 if isinstance(e, AccessDeniedError) else CONSUMER_PAGE_ATTEMPTS
                if attempt >= max_attempts:
                    break
                delay = CONSUMER_RETRY_BACKOFF[min(attempt - 1, len(CONSUMER_RETRY_BACKOFF) - 1)]
                logger.warning(
                    f"Cricinfo commentary page failed (match {match_id}, inning {inning}, from over {from_over}): "
                    f"{type(e).__name__}: {e}. Retrying in {delay:.0f}s (attempt {attempt}/{max_attempts})..."
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _fetch_consumer_commentary(self, series_id: int, match_id: int) -> List[Dict[str, Any]]:
        """Every inning of a match from the Cricinfo consumer API. Raises CricketAPIError if a page cannot be fetched."""
        comments: List[Dict[str, Any]] = []
        for inn in range(1, 5):
            frm = 1
            inn_had_comments = False
            while True:
                res = await self._get_consumer_page(series_id, match_id, inn, frm)
                c_list = (res or {}).get("comments") or []
                if not c_list:
                    break
                inn_had_comments = True
                comments.extend(c_list)
                nxt = (res or {}).get("nextInningOver")
                if not nxt or nxt == -1 or nxt <= frm:
                    break
                frm = nxt
                await asyncio.sleep(0.08)
            if not inn_had_comments and inn >= 2:
                break
        return comments

    @staticmethod
    def _tag_records(records: List[Dict[str, Any]], provider_used: str) -> None:
        """Stamp every delivery with the feed it came from so exports carry their own provenance."""
        for rec in records:
            rec["data_source"] = provider_used
            if provider_used == PROVIDER_CRICINFO:
                rec.setdefault("shotType_derived", None)
                rec["shotType_source"] = "ESPNcricinfo (ball tracking)" if rec.get("shotType") else None

    @staticmethod
    def _has_tracking(records: List[Dict[str, Any]]) -> bool:
        return any(rec.get("pitchLine") is not None for rec in records)

    async def get_ball_commentary_with_provenance(
        self, series_id: int, match_id: int, provider_mode: str = "auto"
    ) -> Dict[str, Any]:
        """
        Ball-by-ball deliveries plus where they came from.

        Returns a dict with keys:
          comments        - list of delivery dicts (each tagged with data_source)
          provider_used   - PROVIDER_CRICINFO, PROVIDER_PUBLIC, PROVIDER_PUBLIC_FALLBACK or PROVIDER_NONE
          fallback_reason - why the public feed was used instead of Cricinfo, or None
          has_tracking    - True when per-ball wagon / pitch / shot fields are present

        Only Cricinfo carries per-ball tracking. In 'auto' mode Cricinfo is retried on
        transient errors and the public ESPN feed is used only when Cricinfo cannot be
        reached; the reason is recorded so the UI can show it instead of failing silently.
        """
        result: Dict[str, Any] = {
            "comments": [],
            "provider_used": PROVIDER_NONE,
            "fallback_reason": None,
            "has_tracking": False,
        }

        if provider_mode == "public_espn":
            comments = await self.public_client.get_play_by_play(series_id, match_id)
            self._tag_records(comments, PROVIDER_PUBLIC)
            result["comments"] = comments
            result["provider_used"] = PROVIDER_PUBLIC if comments else PROVIDER_NONE
            return result

        if provider_mode == "espncricinfo":
            comments = await self._fetch_consumer_commentary(series_id, match_id)
            self._tag_records(comments, PROVIDER_CRICINFO)
            result["comments"] = comments
            result["provider_used"] = PROVIDER_CRICINFO if comments else PROVIDER_NONE
            result["has_tracking"] = self._has_tracking(comments)
            return result

        # Auto: Cricinfo with retries, public ESPN only as a last resort.
        fallback_reason: Optional[str]
        try:
            comments = await self._fetch_consumer_commentary(series_id, match_id)
            if comments:
                self._tag_records(comments, PROVIDER_CRICINFO)
                result["comments"] = comments
                result["provider_used"] = PROVIDER_CRICINFO
                result["has_tracking"] = self._has_tracking(comments)
                return result
            fallback_reason = "Cricinfo returned no deliveries for this match (not started, or no commentary yet)."
        except AccessDeniedError as e:
            fallback_reason = f"Cricinfo refused the request with HTTP 403 even after a retry ({e})."
        except CricketAPIError as e:
            fallback_reason = f"Cricinfo request failed after retries: {type(e).__name__}: {e}"
        logger.warning(f"Match {match_id}: falling back to the public ESPN feed. Reason: {fallback_reason}")

        comments = await self.public_client.get_play_by_play(series_id, match_id)
        self._tag_records(comments, PROVIDER_PUBLIC_FALLBACK)
        result["comments"] = comments
        result["provider_used"] = PROVIDER_PUBLIC_FALLBACK if comments else PROVIDER_NONE
        result["fallback_reason"] = fallback_reason
        return result

    async def get_ball_commentary(
        self, series_id: int, match_id: int, provider_mode: str = "auto"
    ) -> List[Dict[str, Any]]:
        """Backward-compatible wrapper returning only the deliveries. See get_ball_commentary_with_provenance."""
        res = await self.get_ball_commentary_with_provenance(series_id, match_id, provider_mode)
        return res["comments"]

    async def close(self) -> None:
        await self.consumer_series_client.close()
        await self.consumer_match_client.close()
        await self.public_client.close()


# =====================================================================
# Safe Asyncio Runner for Streamlit / Sync Contexts
# =====================================================================

def run_async(coro: Any) -> Any:
    """
    Safely executes an async coroutine within Streamlit or script runners
    without unclosed event-loop errors or dangling connection warnings.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already inside a running loop (e.g. nest_asyncio or custom loop)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

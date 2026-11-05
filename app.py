import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import pandas as pd
import streamlit as st

from apis import (
    PROVIDER_CRICINFO,
    PROVIDER_NONE,
    PROVIDER_PUBLIC,
    PROVIDER_PUBLIC_FALLBACK,
    AccessDeniedError,
    CricketAPIError,
    CricketDataProvider,
    RateLimitError,
    ResourceNotFoundError,
    ServerError,
    run_async,
)
from cricket_data_converter import (
    build_wagon_wheel_summary,
    build_pitch_map_summary,
    build_shot_classification_summary,
    build_field_availability_audit,
    extract_commentary_shot_insights,
    generate_excel_workbook_bytes,
    build_wagon_wheel_plotly_figure,
    normalize_shot_coordinates,
)

logger = logging.getLogger("cricket_app")

CRICINFO_STANDARD_COLUMNS = [
    '_uid', 'id', 'ballsActual', 'oversUnique', 'oversActual', 'overNumber', 'inningNumber', 'ballsUnique', 'ballNumber',
    'totalRuns', 'batsmanRuns', 'isFour', 'isSix', 'isWicket', 'dismissalType', 'byes', 'legbyes', 'wides', 'noballs',
    'penalties', 'wagonX', 'wagonY', 'wagonZone', 'pitchLine', 'pitchLength', 'shotType', 'shotControl', 'timestamp',
    'modified', 'batsmanPlayerId', 'nonStrikerPlayerId', 'bowlerPlayerId', 'outPlayerId', 'totalInningRuns',
    'totalInningWickets', 'title', 'dismissalText', 'commentPreTextItems', 'commentTextItems', 'commentPostTextItems',
    'commentVideos', 'commentImages', 'events', 'over', 'batsmanStatText', 'bowlerStatText', 'smartStats', 'short',
    'long', 'commentary', 'fielderText', 'bowlerText', 'id_1', 'inning', 'id_2', 'objectId', 'scribeId', 'slug',
    'name', 'longName', 'abbreviation', 'unofficialName', 'imageUrl', 'isCountry', 'primaryColor', 'id_3',
    'objectId_1', 'slug_1', 'url', 'width', 'height', 'caption', 'longCaption', 'credit', 'photographer', 'peerUrls',
    'id_4', 'objectId_2', 'name_1', 'shortName', 'abbreviation_1', 'slug_2', 'inningNumber_1', 'overNumber_1',
    'overRuns', 'overWickets', 'totalRuns_1', 'totalWickets', 'target', 'overLimit', 'ballLimit', 'isMaiden',
    'isComplete', 'isSuperOver', 'overEndBatsmen', 'overEndBowlers', 'rollover', 'score', 'winProbability'
]

st.set_page_config(
    page_title="Cricket Intelligence Hub",
    layout="wide",
    initial_sidebar_state="expanded",
)

IMAGE_BASE_URL = "https://img1.hscicdn.com/image/upload"

st.markdown(
    """
    <style>
    .stApp { background-color: #f8fafc; }
    .match-card {
        background-color: white;
        padding: 1.5rem;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        margin-bottom: 1rem;
    }
    div[data-testid="stMetric"] {
        background-color: white;
        border: 1px solid #e2e8f0;
        padding: 15px;
        border-radius: 10px;
    }
    .stButton button {
        border-radius: 8px;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_full_image_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    clean_path = path.lstrip("/")
    return f"{IMAGE_BASE_URL}/{clean_path}"


def extract_series_id(url_or_id: str) -> Optional[str]:
    """Extract numeric series ID from URL or raw ID string."""
    if not url_or_id:
        return None
    cleaned = str(url_or_id).strip()
    if cleaned.isdigit():
        return cleaned

    m = re.search(r"series/[\w-]+-(\d+)", cleaned)
    if m:
        return m.group(1)

    m2 = re.search(r"series/(\d+)", cleaned)
    if m2:
        return m2.group(1)

    m3 = re.search(r"-(\d{5,8})(?:/|$)", cleaned)
    if m3:
        return m3.group(1)

    return None


def process_flat_schema(raw_data_list: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
    if not raw_data_list:
        return pd.DataFrame(), {}
    df = pd.json_normalize(raw_data_list, sep=".")
    df = df.loc[:, ~df.columns.duplicated()]
    hierarchy: Dict[str, List[str]] = {}
    for col in df.columns:
        parent = col.split(".")[0] if "." in col else "Base"
        if parent not in hierarchy:
            hierarchy[parent] = []
        hierarchy[parent].append(col)
    return df, hierarchy


async def fetch_series_matches(
    series_id: int, provider_mode: str = "auto"
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch series schedule using CricketDataProvider with support for
    automatic fallback to authorized ESPN Public Site API on 403 Forbidden.
    """
    provider = CricketDataProvider()
    try:
        res = await provider.get_schedule(series_id, provider_mode=provider_mode)
        results = res.get("content", {}).get("matches", [])
        provider_used = res.get("provider_used", "Standard Provider")

        formatted = []
        for m in results:
            teams = m.get("teams") or []
            t1_team = teams[0].get("team", {}) if len(teams) > 0 and isinstance(teams[0], dict) else {}
            t2_team = teams[1].get("team", {}) if len(teams) > 1 and isinstance(teams[1], dict) else {}

            t1_name = t1_team.get("abbreviation") or t1_team.get("name") or "Team 1"
            t2_name = t2_team.get("abbreviation") or t2_team.get("name") or "Team 2"

            title = m.get("title") or "Match"
            label = f"{title}: {t1_name} v {t2_name}"

            t1_logo = get_full_image_url(t1_team.get("imageUrl")) or ""
            t2_logo = get_full_image_url(t2_team.get("imageUrl")) or ""

            formatted.append({
                "id": m.get("objectId") or m.get("id"),
                "label": label,
                "status": m.get("status") or "UPCOMING",
                "outcome": m.get("statusText") or "",
                "venue": (m.get("ground") or {}).get("name", "Unknown Venue"),
                "t1_logo": t1_logo,
                "t2_logo": t2_logo,
                "provider": provider_used,
            })
        return formatted, provider_used
    finally:
        await provider.close()


async def fetch_ball_by_ball(
    series_id: int,
    match_id: int,
    session: aiohttp.ClientSession,
    provider_mode: str = "auto",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Fetch and enrich ball-by-ball deliveries with player names,
    match metadata, clean commentary, and match summary statistics.

    Returns (deliveries, summary, provenance). provenance records which feed
    served the deliveries, why a fallback happened (if it did), and whether
    per-ball tracking fields are present.
    """
    from apis import (
        AsyncESPNPublicSiteClient,
        AsyncMatchClient,
    )

    match_client = AsyncMatchClient(session=session)
    pub_client = AsyncESPNPublicSiteClient(session=session)

    match_meta: Dict[str, Any] = {}
    player_map: Dict[Any, str] = {}
    summary: Dict[str, Any] = {}
    provenance: Dict[str, Any] = {
        "provider_used": PROVIDER_NONE,
        "fallback_reason": None,
        "has_tracking": False,
        "deliveries": 0,
    }

    # Step 1: Collect match metadata & player names
    try:
        comm = await match_client.get_commentary(id=match_id, series_id=series_id) or {}
        m_meta = comm.get("match", {})
        match_meta["title"] = m_meta.get("title", "")
        match_meta["startDate"] = m_meta.get("startDate", "")
        match_meta["venue"] = (m_meta.get("ground") or {}).get("name", "")

        sc = await match_client.get_scorecard(id=match_id, series_id=series_id) or {}
        sc_content = sc.get("content", {})
        for tp in sc_content.get("matchPlayers", {}).get("teamPlayers", []):
            for p in tp.get("players", []):
                pl = p.get("player", {})
                if pl and pl.get("id"):
                    player_map[pl["id"]] = pl.get("longName") or pl.get("name")
    except Exception as e:
        # Metadata is a nice-to-have; player names also come from the public summary below.
        logger.warning(
            f"Match {match_id}: Cricinfo metadata (commentary/scorecard) unavailable: {type(e).__name__}: {e}"
        )

    # Always fetch summary for roster stats (Wagon Zones, Pitch Map, Preferred Shots)
    try:
        summary = await pub_client.get_match_summary(series_id, match_id) or {}
        header = summary.get("header", {})
        comp = header.get("competitions", [{}])[0] if header.get("competitions") else {}
        if not match_meta.get("title"):
            match_meta["title"] = comp.get("description") or header.get("name", "")
        if not match_meta.get("startDate"):
            match_meta["startDate"] = comp.get("date", "")
        if not match_meta.get("venue"):
            match_meta["venue"] = summary.get("gameInfo", {}).get("venue", {}).get("fullName", "")

        for rst in summary.get("rosters", []):
            for r_entry in rst.get("roster", []):
                ath = r_entry.get("athlete", {})
                if ath and ath.get("id"):
                    ath_id = int(ath["id"]) if str(ath["id"]).isdigit() else ath["id"]
                    if ath_id not in player_map:
                        player_map[ath_id] = ath.get("displayName") or ath.get("fullName")
    except Exception as e:
        logger.warning(f"Could not fetch match summary for match {match_id}: {e}")

    # Step 2: Fetch ball commentary deliveries
    match_deliveries = []
    try:
        provider = CricketDataProvider(session=session)
        res = await provider.get_ball_commentary_with_provenance(
            series_id=series_id, match_id=match_id, provider_mode=provider_mode
        )
        match_deliveries = res["comments"]
        provenance.update(
            provider_used=res["provider_used"],
            fallback_reason=res.get("fallback_reason"),
            has_tracking=bool(res.get("has_tracking")),
            deliveries=len(match_deliveries),
        )
        for c in match_deliveries:
            bat_id = c.get("batsmanPlayerId")
            bowl_id = c.get("bowlerPlayerId")
            out_id = c.get("outPlayerId")

            c.setdefault("match_context.id", match_id)
            c.setdefault("match_context.series_id", series_id)
            c.setdefault("match_context.inning", c.get("inningNumber", 1))
            c.setdefault("match_context.match_title", match_meta.get("title", ""))
            c.setdefault("match_context.venue", match_meta.get("venue", ""))
            c.setdefault("match_context.date", match_meta.get("startDate", ""))

            if bat_id in player_map and not c.get("batsman_name"):
                c["batsman_name"] = player_map[bat_id]
            if bowl_id in player_map and not c.get("bowler_name"):
                c["bowler_name"] = player_map[bowl_id]
            if out_id in player_map and not c.get("player_out_name"):
                c["player_out_name"] = player_map[out_id]

            if not c.get("commentary_clean"):
                c_items = c.get("commentTextItems") or []
                if c_items and isinstance(c_items, list):
                    c["commentary_clean"] = " ".join([
                        re.sub(r"<[^>]+>", "", item.get("html", "")).strip()
                        for item in c_items if isinstance(item, dict)
                    ])
    except Exception as e:
        logger.error(f"Error fetching commentary for match {match_id}: {e}")
        match_deliveries = []
        provenance.update(
            provider_used=PROVIDER_NONE,
            fallback_reason=f"Extraction failed: {type(e).__name__}: {e}",
            has_tracking=False,
            deliveries=0,
        )

    return match_deliveries, summary, provenance


async def batch_extract(
    series_id: int,
    match_ids: List[int],
    provider_mode: str = "auto",
    return_summaries: bool = False,
) -> Union[List[Dict[str, Any]], Tuple[List[Dict[str, Any]], Dict[int, Any], Dict[int, Dict[str, Any]]]]:
    """
    Batch extract ball commentary deliveries and match summaries across multiple matches.
    If return_summaries is True, returns (all_deliveries, summaries_dict, provenance_dict),
    where provenance_dict maps match id to the feed that served it and any fallback reason.
    If return_summaries is False (default), returns all_deliveries for backward compatibility.
    """
    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(3)

        async def extract_single(mid: int):
            async with sem:
                try:
                    deliveries, summary, provenance = await fetch_ball_by_ball(series_id, mid, session, provider_mode)
                    return deliveries, summary, provenance, mid
                except Exception as e:
                    logger.warning(f"Error extracting ball data for match {mid}: {e}")
                    failed = {
                        "provider_used": PROVIDER_NONE,
                        "fallback_reason": f"Extraction failed: {type(e).__name__}: {e}",
                        "has_tracking": False,
                        "deliveries": 0,
                    }
                    return [], {}, failed, mid

        tasks = [extract_single(mid) for mid in match_ids]
        results = await asyncio.gather(*tasks)

        all_deliveries = []
        all_summaries = {}
        all_provenance: Dict[int, Dict[str, Any]] = {}
        for deliveries, summary, provenance, mid in results:
            if isinstance(deliveries, list):
                all_deliveries.extend(deliveries)
            if summary and isinstance(summary, dict):
                all_summaries[mid] = summary
            all_provenance[mid] = provenance

        if return_summaries:
            return all_deliveries, all_summaries, all_provenance
        return all_deliveries


def summarize_ball_sources(
    provenance: Dict[Any, Dict[str, Any]], fixtures: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Group extracted matches by the feed that served their ball data."""
    labels = {f.get("id"): f.get("label", str(f.get("id"))) for f in (fixtures or [])}
    groups: Dict[str, List[Dict[str, Any]]] = {"cricinfo": [], "fallback": [], "empty": []}
    for mid, p in (provenance or {}).items():
        entry = {"id": mid, "label": labels.get(mid, str(mid)), **(p or {})}
        src = entry.get("provider_used")
        if src == PROVIDER_CRICINFO:
            groups["cricinfo"].append(entry)
        elif src in (PROVIDER_PUBLIC, PROVIDER_PUBLIC_FALLBACK):
            groups["fallback"].append(entry)
        else:
            groups["empty"].append(entry)
    return groups


def render_ball_source_banner(
    provenance: Dict[Any, Dict[str, Any]],
    fixtures: List[Dict[str, Any]],
    tracked_rows: Optional[int] = None,
    total_rows: Optional[int] = None,
) -> None:
    """
    Say on screen where the ball data came from. Green when every match came from
    Cricinfo (the only feed with wagon / pitch / shot columns); red, naming each
    match and the reason, when any match was served by the public ESPN feed.
    """
    groups = summarize_ball_sources(provenance, fixtures)
    n_cric, n_fb, n_empty = len(groups["cricinfo"]), len(groups["fallback"]), len(groups["empty"])
    if not (n_cric or n_fb or n_empty):
        return

    if n_fb == 0 and n_cric > 0:
        detail = ""
        if tracked_rows is not None and total_rows:
            detail = (
                f" Tracking columns (wagonX, wagonY, wagonZone, pitchLine, pitchLength, shotType, shotControl) "
                f"are populated on {tracked_rows} of {total_rows} deliveries."
            )
        st.success(f"Ball data source: **{PROVIDER_CRICINFO}** for all {n_cric} match(es).{detail}")
    elif n_fb:
        lines = [
            f"**{n_fb} of {n_cric + n_fb} match(es) were served by the public ESPN feed, "
            "which has NO wagon wheel, pitch map or shot columns.**"
        ]
        for e in groups["fallback"]:
            reason = e.get("fallback_reason")
            if not reason:
                reason = (
                    "Public ESPN feed was selected in the Data Provider dropdown."
                    if e.get("provider_used") == PROVIDER_PUBLIC
                    else "unknown"
                )
            lines.append(f"- {e['label']}: {reason}")
        lines.append(
            "Fix: set Data Provider to **Auto** or **ESPNcricinfo Consumer API** and click "
            "**Download Ball Data** again. If the reason is a 403 or rate limit, wait a minute first."
        )
        st.error("\n".join(lines))

    if n_empty:
        details = []
        for e in groups["empty"]:
            reason = e.get("fallback_reason")
            details.append(f"{e['label']}" + (f" ({reason})" if reason else ""))
        st.warning("No deliveries were retrieved for: " + "; ".join(details))


def main():
    if "last_url" not in st.session_state:
        st.session_state["last_url"] = ""
    if "loaded_config" not in st.session_state:
        st.session_state["loaded_config"] = {}
    if "fixtures" not in st.session_state:
        st.session_state["fixtures"] = []

    with st.sidebar:
        st.markdown("### Data Ingestion")
        url = st.text_input("Series URL or ID", value="https://www.espncricinfo.com/series/ipl-2026-1510719")

        if url != st.session_state["last_url"]:
            st.session_state["last_url"] = url
            if "raw_data" in st.session_state:
                del st.session_state["raw_data"]

        s_id = extract_series_id(url)
        if s_id:
            st.success(f"Detected Series ID: `{s_id}`")

            # Provider selection with automatic fallback recommendation
            provider_choice = st.selectbox(
                "Data Provider",
                options=[
                    "Auto (ESPNcricinfo; public ESPN only if Cricinfo fails)",
                    "ESPNcricinfo Consumer API (strict, no fallback)",
                    "ESPN Public Site API (NO wagon / pitch / shot data)",
                ],
                index=0,
                help=(
                    "Only ESPNcricinfo carries per-ball wagon wheel, pitch map and shot columns. "
                    "'Auto' retries Cricinfo on transient errors and uses the public ESPN feed only when "
                    "Cricinfo cannot be reached; any fallback is reported on screen after extraction."
                ),
            )
            mode_map = {
                "Auto (ESPNcricinfo; public ESPN only if Cricinfo fails)": "auto",
                "ESPNcricinfo Consumer API (strict, no fallback)": "espncricinfo",
                "ESPN Public Site API (NO wagon / pitch / shot data)": "public_espn",
            }
            selected_mode = mode_map[provider_choice]

            sync_col, retry_col = st.columns([2, 1])
            with sync_col:
                sync_clicked = st.button("Sync Match Schedule", use_container_width=True, type="primary")
            with retry_col:
                retry_clicked = st.button("Retry", use_container_width=True)

            if sync_clicked or retry_clicked:
                with st.spinner("Fetching series matches..."):
                    try:
                        fixtures, provider_used = run_async(
                            fetch_series_matches(int(s_id), provider_mode=selected_mode)
                        )
                        st.session_state["fixtures"] = fixtures
                        st.session_state["s_id"] = s_id
                        st.session_state["provider_used"] = provider_used

                        # Reset match selections for fresh schedule
                        for k in list(st.session_state.keys()):
                            if k.startswith("q_"):
                                del st.session_state[k]
                        st.session_state["select_all_matches"] = True

                        if not fixtures:
                            st.warning(f"No fixtures found for Series ID `{s_id}`. Please verify the ID.")
                        else:
                            st.success(f"Loaded {len(fixtures)} fixtures via **{provider_used}**!")
                    except AccessDeniedError as e:
                        st.error(
                            "⚠️ **HTTP 403 Forbidden**: ESPNcricinfo Consumer API is protected by Akamai EdgeAuth/WAF. "
                            "Select **'Auto'** or **'ESPN Public Site API'** to bypass 403 and load real fixtures."
                        )
                    except ResourceNotFoundError:
                        st.error(f"Series ID `{s_id}` was not found (HTTP 404).")
                    except Exception as e:
                        st.error(f"Failed to sync schedule: {e}")

        if st.session_state.get("fixtures"):
            st.divider()
            st.markdown("### Extraction Queue")
            completed = [m for m in st.session_state["fixtures"] if m.get("status") in ("RESULT", "COMPLETED")]
            pool = completed if completed else st.session_state["fixtures"]

            # Deduplicate match IDs in pool while preserving ordering
            seen_ids = set()
            unique_pool = []
            for m in pool:
                mid = m.get("id") or m.get("objectId")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    unique_pool.append(m)
            pool = unique_pool
            pool_ids = [m.get("id") or m.get("objectId") for m in pool]

            # Callback: Toggle all match checkboxes
            def on_select_all_change():
                new_state = st.session_state.get("select_all_matches", True)
                for mid in pool_ids:
                    st.session_state[f"q_{mid}"] = new_state

            # Callback: Sync select_all_matches when an individual checkbox changes
            def on_individual_change():
                st.session_state["select_all_matches"] = (
                    len(pool_ids) > 0 and
                    all(st.session_state.get(f"q_{mid}", False) for mid in pool_ids)
                )

            # Initialize states in session_state before widgets are rendered
            if "select_all_matches" not in st.session_state:
                st.session_state["select_all_matches"] = True

            for mid in pool_ids:
                if f"q_{mid}" not in st.session_state:
                    st.session_state[f"q_{mid}"] = st.session_state["select_all_matches"]

            # Ensure master state accurately reflects current individual selections
            st.session_state["select_all_matches"] = (
                len(pool_ids) > 0 and
                all(st.session_state.get(f"q_{mid}", False) for mid in pool_ids)
            )

            # Master "Select All Available Matches" checkbox
            st.checkbox(
                "Select All Available Matches",
                key="select_all_matches",
                on_change=on_select_all_change,
            )

            # Individual match checkboxes
            selected_ids = []
            for m in pool:
                mid = m.get("id") or m.get("objectId")
                is_checked = st.checkbox(
                    m["label"],
                    key=f"q_{mid}",
                    on_change=on_individual_change,
                )
                if is_checked:
                    selected_ids.append(mid)

            if st.button("Download Ball Data", type="primary", use_container_width=True, disabled=not selected_ids):
                with st.spinner("Extracting match delivery data & analytical summaries..."):
                    st.session_state["provider_mode"] = selected_mode
                    raw_deliveries, raw_summaries, raw_provenance = run_async(
                        batch_extract(
                            int(st.session_state["s_id"]),
                            selected_ids,
                            provider_mode=selected_mode,
                            return_summaries=True,
                        )
                    )
                    st.session_state["raw_data"] = raw_deliveries
                    st.session_state["match_summaries"] = raw_summaries
                    st.session_state["ball_provenance"] = raw_provenance
                    primary_summary = raw_summaries.get(selected_ids[0]) if (selected_ids and raw_summaries) else {}
                    st.session_state["primary_summary"] = primary_summary

                    count_pts = len(raw_deliveries)
                    if count_pts == 0:
                        st.warning("No ball commentary deliveries found for the selected matches (matches may be scheduled/upcoming).")
                    else:
                        st.success(f"Extracted {count_pts} deliveries across {len(selected_ids)} match(es)!")
                        st.toast(f"Extracted {count_pts} deliveries!")
                    render_ball_source_banner(raw_provenance, st.session_state.get("fixtures", []))

        st.divider()
        st.markdown("### Configuration Management")
        uploaded_file = st.file_uploader("Load Mapper JSON", type=["json"])
        if uploaded_file is not None:
            try:
                st.session_state["loaded_config"] = json.load(uploaded_file)
                st.success("Config Loaded Successfully")
            except Exception as e:
                st.error(f"Error loading config: {e}")

    # Main Area
    if st.session_state.get("fixtures"):
        tab_fixtures, tab_workspace = st.tabs(["Fixture Inventory", "Dataset Architect"])

        with tab_fixtures:
            col_list, col_info = st.columns([2, 1])
            with col_list:
                for m in st.session_state["fixtures"]:
                    is_result = m.get("status") in ("RESULT", "COMPLETED")
                    status_color = "#10b981" if is_result else "#f59e0b"
                    status_bg = "#ecfdf5" if is_result else "#fffbeb"
                    t1_img = f'<img src="{m["t1_logo"]}" width="40" style="object-fit: contain;">' if m.get("t1_logo") else ""
                    t2_img = f'<img src="{m["t2_logo"]}" width="40" style="object-fit: contain;">' if m.get("t2_logo") else ""

                    st.markdown(f"""
                    <div class="match-card">
                        <div style="display: flex; align-items: center; justify-content: space-between;">
                            <div style="display: flex; align-items: center; gap: 15px;">
                                {t1_img}
                                <div>
                                    <span style="font-weight: 700; font-size: 1.1rem; color: #1e293b;">{m['label']}</span>
                                    <div style="font-size: 0.85rem; color: #64748b; margin-top: 4px;">{m.get('venue', '')} &bull; {m.get('outcome', '')}</div>
                                </div>
                                {t2_img}
                            </div>
                            <span style="font-size: 0.75rem; font-weight: 800; color: {status_color}; background: {status_bg}; padding: 4px 10px; border-radius: 20px; border: 1px solid {status_color};">
                                {m['status']}
                            </span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

            with col_info:
                st.markdown("#### Series Information")
                st.metric("Total Matches", len(st.session_state["fixtures"]))
                completed_count = sum(1 for m in st.session_state["fixtures"] if m.get("status") in ("RESULT", "COMPLETED"))
                st.metric("Completed Matches", completed_count)
                st.info(f"Fixture list source: **{st.session_state.get('provider_used', 'Auto')}**")
                if st.session_state.get("ball_provenance"):
                    render_ball_source_banner(
                        st.session_state["ball_provenance"], st.session_state.get("fixtures", [])
                    )

        with tab_workspace:
            if "raw_data" in st.session_state and st.session_state["raw_data"]:
                df_flat, hierarchy = process_flat_schema(st.session_state["raw_data"])
                primary_summary = st.session_state.get("primary_summary") or {}

                # 5-Tab Workspace Suite
                subtab_export, subtab_wagon, subtab_pitch, subtab_shots, subtab_audit = st.tabs([
                    "📊 Dataset & Export",
                    "🎯 Wagon Wheel Analytics",
                    "📐 Pitch Map Distribution",
                    "🏏 Shot Classification",
                    "🛡️ Field Availability Audit",
                ])

                # -------------------------------------------------------------
                # SUBTAB 1: Dataset & Export
                # -------------------------------------------------------------
                with subtab_export:
                    mode_c1, mode_c2 = st.columns([3, 1])
                    with mode_c1:
                        dataset_mode = st.radio(
                            "Dataset Export Format",
                            options=[
                                "Full Commentary Dataset (99 Standard Cricinfo Columns - Complete Parity)",
                                "Custom Schema Architecture (Interactive Object & Field Mapping)",
                            ],
                            index=0,
                            horizontal=True,
                            help="Choose 'Full Commentary Dataset' for 100% schema parity with the official Cricinfo ball-by-ball export.",
                        )
                    with mode_c2:
                        st.metric("Total Extracted Deliveries", len(df_flat))

                    if dataset_mode.startswith("Full Commentary Dataset"):
                        # Standard 99-column Cricinfo export format
                        export_df = df_flat.copy()
                        for col in CRICINFO_STANDARD_COLUMNS:
                            if col not in export_df.columns:
                                export_df[col] = None

                        # Order with the 99 standard Cricinfo columns first, then any extra enriched columns
                        standard_present = [c for c in CRICINFO_STANDARD_COLUMNS if c in export_df.columns]
                        extra_present = [c for c in export_df.columns if c not in CRICINFO_STANDARD_COLUMNS]
                        export_df = export_df[standard_present + extra_present]

                        tracked_rows = int(export_df["pitchLine"].notna().sum()) if "pitchLine" in export_df.columns else 0
                        render_ball_source_banner(
                            st.session_state.get("ball_provenance", {}),
                            st.session_state.get("fixtures", []),
                            tracked_rows=tracked_rows,
                            total_rows=len(export_df),
                        )
                        if tracked_rows == 0 and not st.session_state.get("ball_provenance"):
                            st.warning(
                                "Tracking columns (wagonX, wagonY, wagonZone, pitchLine, pitchLength, shotType, shotControl) "
                                "are empty in this dataset. Re-run Download Ball Data with Data Provider set to Auto."
                            )

                        st.markdown(f"#### Complete Dataset Preview ({len(export_df)} deliveries &times; {len(export_df.columns)} columns)")
                        st.dataframe(export_df.head(50), use_container_width=True)

                        c_down1, c_down2, c_down3 = st.columns(3)
                        with c_down1:
                            st.download_button(
                                "Download Complete CSV",
                                export_df.to_csv(index=False).encode("utf-8-sig"),
                                file_name=f"data_{s_id}.csv",
                                type="primary",
                                use_container_width=True,
                            )
                        with c_down2:
                            st.download_button(
                                "Download Complete JSON",
                                data=export_df.to_json(orient="records", indent=2),
                                file_name=f"data_{s_id}.json",
                                mime="application/json",
                                use_container_width=True,
                            )
                        with c_down3:
                            try:
                                excel_bytes = generate_excel_workbook_bytes(
                                    st.session_state["raw_data"],
                                    summary_data=primary_summary,
                                )
                                st.download_button(
                                    "Download Excel Workbook (.xlsx)",
                                    data=excel_bytes,
                                    file_name=f"data_{s_id}_complete.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    use_container_width=True,
                                )
                            except Exception as e:
                                logger.error(f"Error generating Excel workbook: {e}")
                                st.error(f"Error creating Excel workbook: {e}")
                    else:
                        L, R = st.columns([1, 2])
                        with L:
                            st.markdown("#### 1. Schema Definition")

                            if st.session_state.get("loaded_config"):
                                default_parents = [
                                    p for p in hierarchy.keys()
                                    if any(k.startswith(p) for k in st.session_state["loaded_config"].keys())
                                ]
                            else:
                                recommended = ["Base", "match_context", "over", "dismissalText", "team"]
                                default_parents = [p for p in recommended if p in hierarchy]
                                if not default_parents and hierarchy:
                                    default_parents = list(hierarchy.keys())[:3]

                            selected_parents = st.multiselect(
                                "Data Objects",
                                options=sorted(hierarchy.keys()),
                                default=default_parents if default_parents else None,
                            )

                            final_columns = []
                            for p in selected_parents:
                                with st.expander(f"Group: {p}", expanded=True):
                                    if st.session_state.get("loaded_config"):
                                        default_attrs = [
                                            a for a in hierarchy[p]
                                            if a in st.session_state["loaded_config"].keys()
                                        ]
                                    else:
                                        default_attrs = hierarchy[p]

                                    chosen = st.multiselect(
                                        f"Fields in {p}",
                                        options=hierarchy[p],
                                        key=f"p_{p}",
                                        default=default_attrs if default_attrs else None,
                                    )
                                    final_columns.extend(chosen)

                        with R:
                            st.markdown("#### 2. CSV Header Mapping & Preview")
                            if final_columns:
                                mapping = {}
                                grid = st.columns(2)
                                for i, f in enumerate(final_columns):
                                    with grid[i % 2]:
                                        default_val = st.session_state["loaded_config"].get(f, f.split(".")[-1])
                                        mapping[f] = st.text_input(f"Path: {f}", value=default_val, key=f"map_{f}")

                                final_mapping, used = {}, set()
                                for path, req in mapping.items():
                                    name, count = req, 1
                                    while name in used:
                                        name = f"{req}_{count}"
                                        count += 1
                                    final_mapping[path] = name
                                    used.add(name)

                                export_df = df_flat[final_columns].rename(columns=final_mapping)
                                st.dataframe(export_df.head(50), use_container_width=True)

                                c_down1, c_down2 = st.columns(2)
                                with c_down1:
                                    st.download_button(
                                        "Download CSV (Excel/PowerBI)",
                                        export_df.to_csv(index=False).encode("utf-8-sig"),
                                        file_name=f"cricket_data_{s_id}.csv",
                                        type="primary",
                                        use_container_width=True,
                                    )
                                with c_down2:
                                    config_json = json.dumps(mapping, indent=4)
                                    st.download_button(
                                        "Save Config JSON",
                                        data=config_json,
                                        file_name="config.json",
                                        mime="application/json",
                                        use_container_width=True,
                                    )
                            else:
                                st.info("Select at least one Data Object and Field on the left to preview and export.")

                # -------------------------------------------------------------
                # SUBTAB 2: Wagon Wheel Analytics
                # -------------------------------------------------------------
                with subtab_wagon:
                    st.markdown("#### 🎯 Wagon Wheel Analytics & 2D Optical Field Visualization")

                    has_coords = False
                    tracked_count = 0
                    if df_flat is not None and not df_flat.empty and "wagonX" in df_flat.columns and "wagonY" in df_flat.columns:
                        tracked_deliveries = df_flat[df_flat["wagonX"].notna() & df_flat["wagonY"].notna() & ~((df_flat["wagonX"] == 0) & (df_flat["wagonY"] == 0))]
                        tracked_count = len(tracked_deliveries)
                        has_coords = tracked_count > 0

                    if has_coords:
                        st.success(
                            f"✅ **Optical Hawkeye Telemetry Active**: {tracked_count} deliveries with authentic (X, Y) coordinates plotted on the 2D Wagon Wheel."
                        )

                        # Interactive 2D Plot Controls
                        c_bat, c_event, c_persp = st.columns([2, 2, 2])
                        all_batters = ["All Batters (Full Match)"] + sorted([
                            b for b in df_flat["batsman_name"].dropna().unique() if b and b != "Unknown Batter"
                        ])
                        with c_bat:
                            plot_batter = st.selectbox("🎯 Select Batter", options=all_batters, key="plot_wagon_batter")
                        with c_event:
                            plot_event = st.selectbox(
                                "🏏 Filter Shots",
                                options=[
                                    "All Shots",
                                    "Boundaries Only (4s & 6s)",
                                    "Sixes (6s) Only",
                                    "Fours (4s) Only",
                                    "Scoring Shots (Runs > 0)",
                                    "Dot Balls (0 Runs)",
                                    "Wickets Only",
                                ],
                                key="plot_wagon_event",
                            )
                        with c_persp:
                            plot_persp = st.selectbox(
                                "📐 Orientation / Perspective",
                                options=[
                                    "Standard (Raw Coordinates)",
                                    "Batter Perspective (LHB Mirrored for Off/Leg Alignment)",
                                ],
                                key="plot_wagon_persp",
                            )

                        # Metrics row
                        m_shots = tracked_deliveries
                        if plot_batter != "All Batters (Full Match)":
                            m_shots = m_shots[m_shots["batsman_name"] == plot_batter]

                        m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
                        m_col1.metric("Tracked Shots", len(m_shots))
                        m_col2.metric("Wagon Runs", int(m_shots["batsmanRuns"].sum()))
                        m_fours = int((m_shots["isFour"] == True).sum())
                        m_sixes = int((m_shots["isSix"] == True).sum())
                        m_col3.metric("Fours (4s)", m_fours)
                        m_col4.metric("Sixes (6s)", m_sixes)
                        ctrl_shots = int((m_shots["shotControl"] == 1).sum())
                        ctrl_pct = round((ctrl_shots / len(m_shots)) * 100, 1) if len(m_shots) > 0 else 0.0
                        m_col5.metric("Control %", f"{ctrl_pct}%")

                        # Plotly 2D Field rendering
                        fig = build_wagon_wheel_plotly_figure(
                            df=df_flat,
                            batter_name=plot_batter,
                            filter_event=plot_event,
                            perspective=plot_persp,
                        )
                        st.plotly_chart(fig, use_container_width=True)

                        st.markdown("---")
                    else:
                        st.info("ℹ️ Aggregate 8-zone Wagon Wheel distribution calculated from extracted deliveries.")

                    st.markdown("##### 📊 8 Radial Scoring Zones Summary")
                    wagon_df = build_wagon_wheel_summary(primary_summary, df_flat)

                    batters = sorted([b for b in wagon_df["Batter"].unique() if b not in ("No Wagon Wheel Data", "-")])
                    if batters:
                        sel_col1, sel_col2 = st.columns([2, 1])
                        with sel_col1:
                            sel_batter = st.selectbox("Select Batter", options=batters, key="wagon_batter_sel")
                        with sel_col2:
                            bat_w = wagon_df[wagon_df["Batter"] == sel_batter]
                            team_display = bat_w["Team"].iloc[0] if not bat_w.empty else ""
                            st.write(f"**Team**: {team_display}")

                        m1, m2, m3, m4 = st.columns(4)
                        total_w_runs = int(bat_w["Runs Scored"].sum())
                        total_w_shots = int(bat_w["Scoring Shots"].sum())
                        total_w_4s = int(bat_w["Fours (4s)"].sum())
                        total_w_6s = int(bat_w["Sixes (6s)"].sum())
                        m1.metric("Wagon Runs", total_w_runs)
                        m2.metric("Scoring Shots", total_w_shots)
                        m3.metric("Fours (4s)", total_w_4s)
                        m4.metric("Sixes (6s)", total_w_6s)

                        display_cols = [
                            "Zone ID", "Zone Name", "Runs Scored", "Scoring Shots",
                            "Runs %", "Singles (1s)", "Doubles (2s)", "Fours (4s)", "Sixes (6s)"
                        ]
                        st.dataframe(bat_w[display_cols], use_container_width=True)

                        # Chart runs by zone
                        chart_data = bat_w.set_index("Zone Name")["Runs Scored"]
                        st.bar_chart(chart_data)
                    else:
                        st.info("No batter wagon wheel records found.")

                    with st.expander("View Full Match Wagon Wheel Dataset (All Batters)"):
                        st.dataframe(wagon_df, use_container_width=True)

                # -------------------------------------------------------------
                # SUBTAB 3: Pitch Map Distribution
                # -------------------------------------------------------------
                with subtab_pitch:
                    st.markdown("#### 📐 Bowler Pitch Map Distribution (Length & Line Matrix)")
                    pitch_df = build_pitch_map_summary(primary_summary, df_flat)

                    if primary_summary and "rosters" in primary_summary:
                        st.success(
                            "✅ **Source: ESPN Match Summary Rosters** (`rosters.linescores.statistics.bowling.pitchMapRhb/Lhb`). "
                            "Genuine 6 lengths &times; 5 lines delivery matrices extracted."
                        )
                    else:
                        st.info("ℹ️ Pitch map data aggregated from match delivery records.")

                    st.caption(
                        "Delivery-level Hawkeye pitch coordinates (`pitchLine`, `pitchLength`) are restricted by edge security (HTTP 403 Forbidden) on hs-consumer-api. "
                        "Genuine bowler length &times; line matrices (RHB & LHB) are displayed below without data fabrication."
                    )

                    bowlers = sorted([b for b in pitch_df["Bowler"].unique() if b not in ("No Pitch Map Data", "-")])
                    if bowlers:
                        b_col1, b_col2 = st.columns([2, 1])
                        with b_col1:
                            sel_bowler = st.selectbox("Select Bowler", options=bowlers, key="pitch_bowler_sel")
                        with b_col2:
                            sel_hand = st.radio("Facing Batter Stance", options=["RHB (Right-Hand)", "LHB (Left-Hand)"], horizontal=True, key="pitch_hand_sel")
                        hand_key = "RHB" if "RHB" in sel_hand else "LHB"

                        bowl_p = pitch_df[(pitch_df["Bowler"] == sel_bowler) & (pitch_df["Facing Batter Hand"] == hand_key)]
                        if not bowl_p.empty and bowl_p["Deliveries"].sum() > 0:
                            matrix_view = bowl_p.pivot_table(
                                index="Pitch Length",
                                columns="Pitch Line",
                                values="Deliveries",
                                aggfunc="sum",
                                fill_value=0,
                            )
                            st.markdown(f"**Deliveries Bowled Matrix by {sel_bowler} vs {hand_key}**")
                            st.dataframe(matrix_view, use_container_width=True)
                        else:
                            st.info(f"No deliveries recorded for {sel_bowler} against {hand_key} batters.")
                    else:
                        st.info("No pitch map records found.")

                    with st.expander("View Complete Bowler Pitch Map Records (Deliveries, Runs Conceded, Wickets)"):
                        st.dataframe(pitch_df, use_container_width=True)

                # -------------------------------------------------------------
                # SUBTAB 4: Shot Classification
                # -------------------------------------------------------------
                with subtab_shots:
                    st.markdown("#### 🏏 Shot Classification Analytics")
                    shot_df = build_shot_classification_summary(primary_summary, df_flat)
                    comm_shots_df = extract_commentary_shot_insights(df_flat)

                    st.success("✅ **Source: ESPN Match Summary & Commentary Feed**. Batter preferred shots and delivery shot classifications.")
                    st.caption(
                        "Ball-by-ball shot tags (`shotType`, `shotControl`) are restricted on hs-consumer-api. "
                        "Genuine batter preferred shots and NLP-detected commentary shots are presented below."
                    )

                    st.markdown("##### 1. Batter Preferred Shots & Efficiency")
                    st.dataframe(shot_df, use_container_width=True)

                    st.markdown("##### 2. Delivery Commentary Shot Detections")
                    if not comm_shots_df.empty:
                        st.dataframe(comm_shots_df, use_container_width=True)
                    else:
                        st.info("No specific shot classifications detected in delivery commentary text.")

                # -------------------------------------------------------------
                # SUBTAB 5: Field Availability Audit
                # -------------------------------------------------------------
                with subtab_audit:
                    st.markdown("#### 🛡️ Granular Field-Level Data Availability & Quality Audit")
                    st.caption(
                        "Complete field-by-field verification of retrieved data, genuine sources, non-null counts, "
                        "and technical reasons for unavailable or restricted fields."
                    )
                    prov_groups = summarize_ball_sources(
                        st.session_state.get("ball_provenance", {}), st.session_state.get("fixtures", [])
                    )
                    if prov_groups["fallback"]:
                        ball_src = prov_groups["fallback"][0].get("provider_used")
                        fb_reason = prov_groups["fallback"][0].get("fallback_reason")
                    elif prov_groups["cricinfo"]:
                        ball_src, fb_reason = PROVIDER_CRICINFO, None
                    else:
                        ball_src, fb_reason = None, None
                    audit_df = build_field_availability_audit(
                        df_flat,
                        primary_summary,
                        provider_source=ball_src or st.session_state.get("provider_used", "ESPN Public Site API"),
                        ball_data_source=ball_src,
                        fallback_reason=fb_reason,
                    )

                    a_c1, a_c2, a_c3, a_c4 = st.columns(4)
                    total_fields = len(audit_df)
                    avail_count = len(audit_df[audit_df["Availability Status"] == "Successfully Extracted"])
                    part_count = len(audit_df[audit_df["Availability Status"] == "Partially Available"])
                    gated_statuses = ["Provider Access Denied", "Not Retrieved (Fallback Feed)"]
                    gated_count = len(audit_df[audit_df["Availability Status"].isin(gated_statuses)])

                    a_c1.metric("Audited Fields", total_fields)
                    a_c2.metric("Successfully Extracted", avail_count)
                    a_c3.metric("Partially Available", part_count)
                    a_c4.metric("Not Retrieved (Fallback Feed)", gated_count)

                    filter_choice = st.radio(
                        "Filter Audit View",
                        options=["All Fields", "Successfully Extracted", "Partially Available", "Not Retrieved (Fallback Feed)"],
                        horizontal=True,
                        key="audit_filter_sel",
                    )
                    if filter_choice == "Successfully Extracted":
                        display_audit = audit_df[audit_df["Availability Status"] == "Successfully Extracted"]
                    elif filter_choice == "Partially Available":
                        display_audit = audit_df[audit_df["Availability Status"] == "Partially Available"]
                    elif filter_choice == "Not Retrieved (Fallback Feed)":
                        display_audit = audit_df[audit_df["Availability Status"].isin(gated_statuses)]
                    else:
                        display_audit = audit_df

                    st.dataframe(display_audit, use_container_width=True)

                    st.download_button(
                        "Download Field Availability Audit CSV",
                        audit_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"audit_field_availability_{s_id}.csv",
                        use_container_width=True,
                    )
            else:
                st.warning("Extract match data from the sidebar to begin dataset architecture.")
    else:
        st.write("Initialize Series URL or ID in the sidebar to proceed.")


if __name__ == "__main__":
    main()

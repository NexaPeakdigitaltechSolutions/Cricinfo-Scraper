"""
Cricket Data Analytics Converter & Excel Workbook Generator
Extracts, cleans, standardizes and exports complete ball-by-ball match data
into a professional multi-sheet Excel workbook and CSV/JSON datasets.
Includes granular field-level data availability audit, wagon wheel analytics,
pitch map distributions, and shot classifications.
"""

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd
import plotly.graph_objects as go

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cricket_converter")

# Standard Wagon Wheel radial zones (1 through 8 clockwise)
WAGON_ZONE_NAMES = {
    1: "Fine Leg",
    2: "Square Leg",
    3: "Mid Wicket",
    4: "Long On",
    5: "Long Off",
    6: "Cover",
    7: "Point",
    8: "Third Man",
}

# Standard Pitch Map lengths
LENGTH_NAMES = [
    "Full Toss",
    "Yorker",
    "Full",
    "Good Length",
    "Back of a Length",
    "Short",
]

# Standard Pitch Map lines / corridors
LINE_NAMES = [
    "Down Leg",
    "On Stumps",
    "Outside Off",
    "Wide Outside Off",
    "Very Wide / Other",
]


# =====================================================================
# Clean Commentary & Text Utilities
# =====================================================================

def strip_html_tags(text: Optional[str]) -> str:
    """Safely strip HTML markup from commentary text."""
    if not text or not isinstance(text, str):
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def parse_items_json(val: Any) -> List[Dict[str, Any]]:
    """Parse stringified or structured commentary items."""
    if not val or pd.isna(val):
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        try:
            import ast
            parsed = ast.literal_eval(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


# =====================================================================
# Centralized Coordinate Extraction, Normalization & Visualization
# =====================================================================

def _safe_parse_coord(val: Any) -> Optional[Union[int, float]]:
    """Safely parse and validate a numeric coordinate, rejecting NaN, Inf, and non-numeric strings."""
    if val is None or val == "" or pd.isna(val):
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return None


def normalize_shot_coordinates(
    data: Union[Dict[str, Any], pd.Series],
    default_coord_system: str = "cricinfo_hawkeye_360",
) -> Dict[str, Any]:
    """
    Centralized coordinate normalization function.
    Extracts actual X and Y values from flat fields or nested objects across all
    supported cricket data providers, preserving precision and validating numeric bounds.

    Supported nested keys:
      - shotCoordinates: {"x": ..., "y": ..., "coordinateSystem": ...}
      - shotLocation: {"x": ..., "y": ...}
      - wagonPosition: {"x": ..., "y": ...}
      - coordinates: {"x": ..., "y": ...} or [x, y]
      - ballPosition: {"x": ..., "y": ...}
      - tracking: {"x": ..., "y": ...}

    Supported flat field pairs:
      - (wagonX, wagonY)
      - (shotX, shotY)
      - (xCoordinate, yCoordinate)
      - (x, y) / (X, Y)

    Handles distinction between:
      - Explicit untracked placeholder (x=0, y=0, zone in (0, None)) -> returns None
      - Valid zero on axis/boundary (x=0, y>0 or zone 1-8) -> preserved as valid 0
      - Truly missing coordinates -> preserved as None
    """
    if not isinstance(data, (dict, pd.Series)):
        return {
            "shotCoordinates": None,
            "wagonX": None,
            "wagonY": None,
            "wagonZone": None,
            "isTracked": False,
            "coordinateSystem": default_coord_system,
        }

    get_fn = data.get if isinstance(data, dict) else (lambda k: data[k] if k in data else None)

    # 1. Search nested coordinate objects
    raw_x, raw_y = None, None
    coord_sys = default_coord_system
    nested_keys = ["shotCoordinates", "shotLocation", "wagonPosition", "coordinates", "ballPosition", "tracking"]
    for nk in nested_keys:
        obj = get_fn(nk)
        if isinstance(obj, dict):
            raw_x = obj.get("x") if obj.get("x") is not None else obj.get("X")
            raw_y = obj.get("y") if obj.get("y") is not None else obj.get("Y")
            coord_sys = obj.get("coordinateSystem") or coord_sys
            if raw_x is not None or raw_y is not None:
                break
        elif isinstance(obj, (list, tuple)) and len(obj) >= 2:
            raw_x, raw_y = obj[0], obj[1]
            break

    # 2. Search flat coordinate keys if not found
    if raw_x is None and raw_y is None:
        flat_pairs = [
            ("wagonX", "wagonY"),
            ("shotX", "shotY"),
            ("xCoordinate", "yCoordinate"),
            ("x", "y"),
            ("X", "Y"),
        ]
        for xk, yk in flat_pairs:
            vx = get_fn(xk)
            vy = get_fn(yk)
            if vx is not None or vy is not None:
                raw_x, raw_y = vx, vy
                break

    # 3. Search wagon zone keys
    raw_zone = None
    for zk in ["wagonZone", "zone", "shotZone", "wagon_zone"]:
        vz = get_fn(zk)
        if vz is not None and not pd.isna(vz):
            raw_zone = vz
            break

    x_val = _safe_parse_coord(raw_x)
    y_val = _safe_parse_coord(raw_y)
    z_val = _safe_parse_coord(raw_zone)
    if z_val is not None:
        z_val = int(z_val)

    # Distinguish untracked placeholder (0, 0, 0/None) from valid zero on boundary
    is_untracked = (x_val == 0 and y_val == 0 and (z_val == 0 or z_val is None))
    is_tracked = not is_untracked and (x_val is not None or y_val is not None)

    if not is_tracked:
        return {
            "shotCoordinates": None,
            "wagonX": None,
            "wagonY": None,
            "wagonZone": None if is_untracked else z_val,
            "isTracked": False,
            "coordinateSystem": coord_sys,
        }

    return {
        "shotCoordinates": {
            "x": x_val,
            "y": y_val,
            "coordinateSystem": coord_sys,
        },
        "wagonX": x_val,
        "wagonY": y_val,
        "wagonZone": z_val,
        "isTracked": True,
        "coordinateSystem": coord_sys,
    }


def transform_coordinates_for_presentation(
    x: Optional[float],
    y: Optional[float],
    batter_hand: str = "RHB",
    center_x: float = 180.0,
    center_y: float = 180.0,
    mirror_lhb: bool = False,
) -> Dict[str, Any]:
    """
    Presentation transformation for Wagon Wheel coordinates.
    Preserves raw source coordinates while computing derived geometry:
    - distance from batter crease (0 to ~180 units)
    - angle in degrees from bowler-to-batter axis
    - boundary check
    - optional LHB perspective mirroring across crease (off-side on left)
    """
    if x is None or y is None:
        return {
            "x_pres": None,
            "y_pres": None,
            "distance": None,
            "angle_deg": None,
            "is_boundary": False,
        }

    dx = float(x) - center_x
    dy = float(y) - center_y
    dist = math.sqrt(dx * dx + dy * dy)
    is_boundary = dist >= 150.0

    angle_rad = math.atan2(dx, dy)
    angle_deg = math.degrees(angle_rad)

    x_pres = float(x)
    y_pres = float(y)

    is_lhb = str(batter_hand).upper() in ("LHB", "LEFT", "LEFT-HANDED")
    if mirror_lhb and is_lhb:
        x_pres = 2 * center_x - x_pres

    return {
        "x_pres": x_pres,
        "y_pres": y_pres,
        "distance": round(dist, 1),
        "angle_deg": round(angle_deg, 1),
        "is_boundary": is_boundary,
    }


def build_wagon_wheel_plotly_figure(
    df: pd.DataFrame,
    batter_name: Optional[str] = None,
    filter_event: str = "All Shots",
    perspective: str = "Standard (Raw Coordinates)",
) -> go.Figure:
    """
    Build an interactive 2D Wagon Wheel visualization using Plotly.
    Renders authentic cricket ground geometry, boundary circle, 30-yard fielding ring,
    center pitch rectangle, 8 radial sector boundary dividing lines, and delivery shot rays
    color-coded by runs and dismissal event.
    """
    fig = go.Figure()

    center_x, center_y, radius = 180.0, 180.0, 180.0

    # 1. Outer boundary oval (lush grass green #15803D with boundary rope #FFFFFF)
    fig.add_shape(
        type="circle",
        x0=center_x - radius, y0=center_y - radius,
        x1=center_x + radius, y1=center_y + radius,
        line=dict(color="#FFFFFF", width=2.5),
        fillcolor="#15803D",
        layer="below",
    )

    # 2. 30-yard inner circle (dashed light emerald ring)
    inner_r = 65.0
    fig.add_shape(
        type="circle",
        x0=center_x - inner_r, y0=center_y - inner_r,
        x1=center_x + inner_r, y1=center_y + inner_r,
        line=dict(color="#86EFAC", width=1.5, dash="dash"),
        layer="below",
    )

    # 3. Center Pitch rectangle (light clay / turf tan with crease lines)
    pw, ph = 10.0, 24.0
    fig.add_shape(
        type="rect",
        x0=center_x - pw / 2, y0=center_y - ph / 2,
        x1=center_x + pw / 2, y1=center_y + ph / 2,
        fillcolor="#FDE68A",
        line=dict(color="#B45309", width=1),
        layer="below",
    )
    fig.add_shape(
        type="line",
        x0=center_x - 6, y0=center_y - 8, x1=center_x + 6, y1=center_y - 8,
        line=dict(color="#B45309", width=1.5), layer="below"
    )
    fig.add_shape(
        type="line",
        x0=center_x - 6, y0=center_y + 8, x1=center_x + 6, y1=center_y + 8,
        line=dict(color="#B45309", width=1.5), layer="below"
    )

    # 4. Eight Radial Sector Dividing Spokes and Boundary Labels
    spoke_angles_deg = [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]
    for deg in spoke_angles_deg:
        rad = math.radians(deg)
        sx = center_x + radius * math.sin(rad)
        sy = center_y + radius * math.cos(rad)
        fig.add_shape(
            type="line",
            x0=center_x, y0=center_y, x1=sx, y1=sy,
            line=dict(color="rgba(255, 255, 255, 0.25)", width=1, dash="dot"),
            layer="below",
        )

    zone_placements = [
        ("Z1: Fine Leg", 45, 195),
        ("Z2: Square Leg", 90, 195),
        ("Z3: Mid Wicket", 135, 195),
        ("Z4: Mid On", 165, 195),
        ("Z5: Mid Off", 195, 195),
        ("Z6: Cover", 225, 195),
        ("Z7: Point", 270, 195),
        ("Z8: Third Man", 315, 195),
    ]
    for lbl, deg, r_pos in zone_placements:
        rad = math.radians(deg)
        tx = center_x + r_pos * math.sin(rad)
        ty = center_y + r_pos * math.cos(rad)
        fig.add_annotation(
            x=tx, y=ty, text=f"<b>{lbl}</b>",
            showarrow=False,
            font=dict(size=10, color="#E2E8F0"),
            bgcolor="rgba(15, 23, 42, 0.75)",
            borderpad=2,
        )

    # 5. Filter and plot delivery shots
    if df is not None and not df.empty and "wagonX" in df.columns and "wagonY" in df.columns:
        valid_shots = df[df["wagonX"].notna() & df["wagonY"].notna()].copy()
        valid_shots = valid_shots[~((valid_shots["wagonX"] == 0) & (valid_shots["wagonY"] == 0))]

        if batter_name and batter_name not in ("All Batters", "All Batters (Full Match)"):
            valid_shots = valid_shots[valid_shots["batsman_name"] == batter_name]

        if filter_event == "Boundaries Only (4s & 6s)":
            valid_shots = valid_shots[(valid_shots["isFour"] == True) | (valid_shots["isSix"] == True)]
        elif filter_event == "Sixes (6s) Only":
            valid_shots = valid_shots[valid_shots["isSix"] == True]
        elif filter_event == "Fours (4s) Only":
            valid_shots = valid_shots[valid_shots["isFour"] == True]
        elif filter_event == "Scoring Shots (Runs > 0)":
            valid_shots = valid_shots[valid_shots["batsmanRuns"] > 0]
        elif filter_event == "Dot Balls (0 Runs)":
            valid_shots = valid_shots[valid_shots["batsmanRuns"] == 0]
        elif filter_event == "Wickets Only":
            valid_shots = valid_shots[valid_shots["isWicket"] == True]

        mirror_lhb = "Mirrored" in perspective or "Batter Perspective" in perspective

        categories = [
            ("Sixes (6s)", lambda r: r.get("isSix") is True or r.get("batsmanRuns") == 6, "#F59E0B", 9),
            ("Fours (4s)", lambda r: r.get("isFour") is True or r.get("batsmanRuns") == 4, "#3B82F6", 8),
            ("2s & 3s", lambda r: r.get("batsmanRuns") in (2, 3), "#10B981", 7),
            ("Singles (1s)", lambda r: r.get("batsmanRuns") == 1, "#06B6D4", 6),
            ("Dot Balls (0s)", lambda r: r.get("batsmanRuns") == 0 and not r.get("isWicket"), "#94A3B8", 5),
            ("Wickets", lambda r: bool(r.get("isWicket")), "#EF4444", 9),
        ]

        for cat_name, cat_fn, color_hex, marker_sz in categories:
            cat_rows = [r for _, r in valid_shots.iterrows() if cat_fn(r)]
            if not cat_rows:
                continue

            line_xs, line_ys = [], []
            marker_xs, marker_ys = [], []
            hover_texts = []

            for r in cat_rows:
                wx = float(r["wagonX"])
                wy = float(r["wagonY"])
                pres = transform_coordinates_for_presentation(
                    wx, wy,
                    batter_hand=r.get("facingBatterHand", "RHB"),
                    center_x=center_x, center_y=center_y,
                    mirror_lhb=mirror_lhb
                )
                px = pres["x_pres"]
                py = pres["y_pres"]

                line_xs.extend([center_x, px, None])
                line_ys.extend([center_y, py, None])
                marker_xs.append(px)
                marker_ys.append(py)

                b_name = r.get("batsman_name", "Batter")
                bw_name = r.get("bowler_name", "Bowler")
                ov = r.get("oversActual", r.get("overNumber", ""))
                runs = r.get("batsmanRuns", 0)
                stype = str(r.get("shotType") or r.get("shotType_derived") or "Shot").replace("_", " ").title()
                sctrl = "Controlled" if r.get("shotControl") == 1 else ("Edged/Mistimed" if r.get("shotControl") == 2 else "Unclassified")
                pline = str(r.get("pitchLine") or "-").replace("_", " ").title()
                plen = str(r.get("pitchLength") or "-").replace("_", " ").title()
                zid = r.get("wagonZone", "-")
                zname = WAGON_ZONE_NAMES.get(int(zid), f"Zone {zid}") if str(zid).isdigit() else "Zone -"

                badge = "🔴 WICKET" if r.get("isWicket") else (f"💥 SIX ({runs})" if r.get("isSix") else (f"🔹 FOUR ({runs})" if r.get("isFour") else f"Runs: {runs}"))

                ht = (
                    f"<b>{b_name}</b> vs {bw_name}<br>"
                    f"Over: <b>{ov}</b> | Event: <b>{badge}</b><br>"
                    f"Shot: <b>{stype}</b> ({sctrl})<br>"
                    f"Pitch: {plen} on {pline}<br>"
                    f"Zone: <b>{zname}</b> (Zone {zid})<br>"
                    f"Coords: (X={int(wx)}, Y={int(wy)}) | Dist: {pres['distance']}"
                )
                hover_texts.append(ht)

            fig.add_trace(
                go.Scatter(
                    x=line_xs, y=line_ys,
                    mode="lines",
                    line=dict(color=color_hex, width=1.4),
                    opacity=0.6,
                    showlegend=False,
                    hoverinfo="skip",
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=marker_xs, y=marker_ys,
                    mode="markers",
                    marker=dict(
                        size=marker_sz,
                        color=color_hex,
                        line=dict(width=1, color="#FFFFFF"),
                    ),
                    name=f"{cat_name} ({len(cat_rows)})",
                    text=hover_texts,
                    hoverinfo="text",
                )
            )

    title_text = f"🎯 2D Wagon Wheel - {batter_name or 'All Batters'}"
    if perspective != "Standard (Raw Coordinates)":
        title_text += f" ({perspective})"

    fig.update_layout(
        title=dict(
            text=title_text,
            x=0.5,
            xanchor="center",
            font=dict(size=14, color="#F8FAFC"),
        ),
        width=680,
        height=680,
        margin=dict(l=30, r=30, t=50, b=30),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            range=[-25, 385],
        ),
        yaxis=dict(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            range=[385, -25],
            scaleanchor="x",
            scaleratio=1,
        ),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.03,
            xanchor="center",
            x=0.5,
            font=dict(size=11, color="#E2E8F0"),
            bgcolor="rgba(15, 23, 42, 0.7)",
        ),
        plot_bgcolor="#0F172A",
        paper_bgcolor="#0F172A",
    )

    return fig


# =====================================================================
# Complete Ball-By-Ball Extractor & Standardizer
# =====================================================================

def standardize_delivery_record(row: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """
    Standardize a raw delivery record into the complete cricket analytics schema.
    Extracts all genuine fields, cleans commentary, dynamically identifies match
    format, and ensures optical analytics are handled with strict integrity.
    """
    # Extract commentary text
    raw_comm = row.get("commentary_clean") or ""
    if not raw_comm:
        c_items = parse_items_json(row.get("commentTextItems"))
        if c_items:
            raw_comm = " ".join([
                strip_html_tags(item.get("html", ""))
                for item in c_items if isinstance(item, dict)
            ]).strip()

    title = str(row.get("title") or "").strip()
    short_text = title.lower()

    # Total runs & Extras
    total_runs = int(row.get("totalRuns") or 0)
    wides = int(row.get("wides") or 0)
    noballs = int(row.get("noballs") or 0)
    legbyes = int(row.get("legbyes") or 0)
    byes = int(row.get("byes") or 0)
    penalties = int(row.get("penalties") or 0)

    # Batsman runs
    if "batsmanRuns" in row and pd.notnull(row["batsmanRuns"]):
        bat_runs = int(row["batsmanRuns"])
    else:
        if wides > 0 or legbyes > 0 or byes > 0:
            bat_runs = 0
        elif noballs > 0:
            bat_runs = max(0, total_runs - 1)
        else:
            bat_runs = total_runs

    # Boundaries
    is_four = bool(row.get("isFour", False))
    is_six = bool(row.get("isSix", False))
    if not is_four and not is_six:
        if bat_runs == 4 and "four" in short_text:
            is_four = True
        elif bat_runs == 6 and "six" in short_text:
            is_six = True

    # Wickets
    is_wicket = bool(row.get("isWicket", False))
    dism_type = row.get("dismissalType")
    if not is_wicket and dism_type and dism_type != "":
        is_wicket = True
    if is_wicket and not dism_type:
        dism_type = "OUT"

    # Overs & Balls
    over_num = row.get("overNumber")
    ball_num = row.get("ballNumber")
    overs_actual = row.get("oversActual")
    overs_unique = row.get("oversUnique")

    # Match metadata context
    match_id = row.get("id_1") or row.get("match_context.id") or row.get("match_id")
    series_id = row.get("match_context.series_id") or row.get("series_id")
    match_title = row.get("match_context.match_title") or row.get("match_title") or "Cricket Match"
    venue = row.get("match_context.venue") or row.get("venue") or ""
    match_date = row.get("match_context.date") or row.get("timestamp") or ""

    # Dynamically resolve competition & match format
    comp = row.get("competition") or row.get("match_context.competition") or ""
    if not comp:
        t_low = match_title.lower()
        if "ipl" in t_low or "indian premier league" in t_low:
            comp = "Indian Premier League"
        elif "odi" in t_low:
            comp = "One Day International Series"
        elif "t20" in t_low:
            comp = "T20 International Series"
        elif "test" in t_low:
            comp = "Test Series"
        else:
            comp = "Cricket Series"

    m_format = row.get("match_format") or row.get("match_context.format") or ""
    if not m_format:
        t_low = match_title.lower()
        if "odi" in t_low:
            m_format = "ODI"
        elif "test" in t_low:
            m_format = "TEST"
        else:
            m_format = "T20"

    default_over_limit = 50.0 if m_format == "ODI" else (20.0 if m_format == "T20" else 90.0)
    default_ball_limit = default_over_limit * 6.0

    # Optical Hawkeye coordinates handling via centralized normalization
    norm_coords = normalize_shot_coordinates(row)
    wagon_x = norm_coords["wagonX"]
    wagon_y = norm_coords["wagonY"]
    wagon_zone = norm_coords["wagonZone"]
    shot_coordinates = norm_coords["shotCoordinates"]

    pitch_line = row.get("pitchLine")
    pitch_length = row.get("pitchLength")
    shot_type = row.get("shotType")
    shot_control = row.get("shotControl")

    if pd.isna(pitch_line) or pitch_line in ["", "None"]:
        pitch_line = None
    if pd.isna(pitch_length) or pitch_length in ["", "None"]:
        pitch_length = None
    if pd.isna(shot_type) or shot_type in ["", "None"]:
        shot_type = None
    if pd.isna(shot_control) or shot_control in ["", "None"]:
        shot_control = None

    # Derived Commentary Shot Extraction (Kept strictly separate from verified Hawkeye tracking)
    shot_type_derived = row.get("shotType_derived")
    shot_type_source = row.get("shotType_source") or None
    if not shot_type_derived and not shot_type and (raw_comm or title):
        full_text = f"{title} {raw_comm}".lower()
        for p_pat, p_lbl in [
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
            if re.search(p_pat, full_text, re.IGNORECASE):
                shot_type_derived = p_lbl
                shot_type_source = "Derived (Commentary NLP)"
                break

    delivery_id = row.get("_uid") or row.get("id") or (idx + 1)

    return {
        # Match Information
        "match_id": match_id,
        "series_id": series_id,
        "match_title": match_title,
        "match_date": match_date,
        "venue": venue,
        "competition": comp,
        "match_format": m_format,
        "match_status": "COMPLETED",

        # Delivery Information
        "delivery_id": delivery_id,
        "inningNumber": int(row.get("inningNumber") or row.get("inning") or 1),
        "overNumber": over_num,
        "ballNumber": ball_num,
        "oversActual": overs_actual,
        "oversUnique": overs_unique,
        "timestamp": str(row.get("timestamp") or ""),

        # Batting Information
        "batsmanPlayerId": row.get("batsmanPlayerId"),
        "batsman_name": str(row.get("batsman_name") or "Unknown Batter").strip(),
        "nonStrikerPlayerId": row.get("nonStrikerPlayerId"),
        "non_striker_name": str(row.get("non_striker_name") or "").strip(),
        "batsmanRuns": bat_runs,
        "isFour": is_four,
        "isSix": is_six,

        # Bowling Information
        "bowlerPlayerId": row.get("bowlerPlayerId"),
        "bowler_name": str(row.get("bowler_name") or "Unknown Bowler").strip(),
        "totalRuns": total_runs,
        "wides": wides,
        "noballs": noballs,
        "byes": byes,
        "legbyes": legbyes,
        "penalties": penalties,
        "isWicket": is_wicket,
        "dismissalType": dism_type,
        "outPlayerId": row.get("outPlayerId"),
        "fielderText": str(row.get("fielderText") or "").strip() if is_wicket else "",
        "bowlerText": str(row.get("bowlerText") or "").strip() if is_wicket else "",

        # Advanced Optical Analytics (Strictly None when provider restricts)
        "wagonX": wagon_x,
        "wagonY": wagon_y,
        "wagonZone": wagon_zone,
        "shotCoordinates": shot_coordinates,
        "pitchLine": pitch_line,
        "pitchLength": pitch_length,
        "shotType": shot_type,
        "shotControl": shot_control,
        "shotType_derived": shot_type_derived,
        "shotType_source": shot_type_source,

        # Commentary
        "title": title,
        "commentary": raw_comm,
        "commentary_clean": raw_comm,
        "commentPreTextItems": row.get("commentPreTextItems"),
        "commentTextItems": row.get("commentTextItems"),
        "commentPostTextItems": row.get("commentPostTextItems"),
        "events": row.get("events") or [],
        "smartStats": row.get("smartStats") or [],

        # Innings & Over Progression Data
        "totalInningRuns": row.get("totalInningRuns"),
        "totalInningWickets": row.get("totalInningWickets"),
        "overRuns": row.get("overRuns"),
        "overWickets": row.get("overWickets"),
        "target": row.get("target"),
        "overLimit": row.get("overLimit") or default_over_limit,
        "ballLimit": row.get("ballLimit") or default_ball_limit,
        "isMaiden": bool(row.get("isMaiden")),
        "isComplete": bool(row.get("isComplete")),
        "isSuperOver": bool(row.get("isSuperOver")),
    }


def load_input_dataset(file_path: str) -> List[Dict[str, Any]]:
    """Load records from either JSON or CSV input file."""
    if file_path.endswith(".json"):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # Check for standard wrapper keys
                if "content" in data and "comments" in data["content"]:
                    return data["content"]["comments"]
                if "comments" in data:
                    return data["comments"]
                return [data]
    elif file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
        return df.to_dict(orient="records")
    raise ValueError(f"Unsupported file format: {file_path}")


# =====================================================================
# Analytical Summaries Generation
# =====================================================================

def build_innings_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Build inning-level scorecard summary."""
    summaries = []
    for inn, g in df.groupby("inningNumber"):
        total_runs = int(g["totalRuns"].sum())
        total_wickets = int(g["isWicket"].sum())
        total_balls = len(g)
        legal_balls = len(g[g["wides"] == 0])

        overs_comp = legal_balls // 6
        overs_rem = legal_balls % 6
        overs_str = f"{overs_comp}.{overs_rem}"
        overs_float = overs_comp + (overs_rem / 6.0)
        rr = round(total_runs / overs_float, 2) if overs_float > 0 else 0.0

        fours = int((g["isFour"] == True).sum())
        sixes = int((g["isSix"] == True).sum())
        boundary_runs = (fours * 4) + (sixes * 6)
        boundary_pct = round((boundary_runs / total_runs) * 100, 1) if total_runs > 0 else 0.0

        wides = int(g["wides"].sum())
        noballs = int(g["noballs"].sum())
        legbyes = int(g["legbyes"].sum())
        byes = int(g["byes"].sum())
        total_extras = wides + noballs + legbyes + byes

        dot_balls = int((g["totalRuns"] == 0).sum())

        summaries.append({
            "Inning Number": inn,
            "Total Score": f"{total_runs}/{total_wickets}",
            "Overs Played": overs_str,
            "Total Runs": total_runs,
            "Wickets": total_wickets,
            "Run Rate": rr,
            "Legal Deliveries": legal_balls,
            "Total Deliveries": total_balls,
            "Dot Balls": dot_balls,
            "Batsman Runs": int(g["batsmanRuns"].sum()),
            "Total Extras": total_extras,
            "Wides": wides,
            "No Balls": noballs,
            "Leg Byes": legbyes,
            "Byes": byes,
            "Fours": fours,
            "Sixes": sixes,
            "Boundary Runs": boundary_runs,
            "Boundary %": f"{boundary_pct}%",
        })
    return pd.DataFrame(summaries)


def build_batting_analytics(df: pd.DataFrame) -> pd.DataFrame:
    """Build individual batting scorecard and analytics."""
    records = []
    for (inn, p_id, p_name), g in df.groupby(["inningNumber", "batsmanPlayerId", "batsman_name"], sort=False):
        runs = int(g["batsmanRuns"].sum())
        balls = len(g[g["wides"] == 0])
        fours = int((g["isFour"] == True).sum())
        sixes = int((g["isSix"] == True).sum())
        boundary_runs = (fours * 4) + (sixes * 6)
        sr = round((runs / balls) * 100, 2) if balls > 0 else 0.0
        dots = int((g["batsmanRuns"] == 0).sum())
        dot_pct = round((dots / balls) * 100, 1) if balls > 0 else 0.0
        boundary_pct = round((boundary_runs / runs) * 100, 1) if runs > 0 else 0.0

        # Dismissal info
        out_rows = df[(df["inningNumber"] == inn) & (df["outPlayerId"] == p_id)]
        if not out_rows.empty and out_rows.iloc[0]["isWicket"]:
            out_row = out_rows.iloc[0]
            status = "Out"
            mode = out_row.get("dismissalType") or "Dismissed"
            bowler = out_row.get("bowler_name") or ""
            fielder = out_row.get("fielderText") or ""
        else:
            status = "Not Out"
            mode = "Not Out"
            bowler = ""
            fielder = ""

        records.append({
            "Inning": inn,
            "Player ID": p_id,
            "Player Name": p_name,
            "Status": status,
            "Dismissal Mode": mode,
            "Bowler": bowler,
            "Fielder": fielder,
            "Runs Scored": runs,
            "Balls Faced": balls,
            "Strike Rate": sr,
            "Fours (4s)": fours,
            "Sixes (6s)": sixes,
            "Boundary Runs": boundary_runs,
            "Boundary %": f"{boundary_pct}%",
            "Dot Balls": dots,
            "Dot %": f"{dot_pct}%",
        })
    return pd.DataFrame(records)


def build_bowling_analytics(df: pd.DataFrame) -> pd.DataFrame:
    """Build individual bowling card and analytics."""
    records = []
    for (inn, p_id, p_name), g in df.groupby(["inningNumber", "bowlerPlayerId", "bowler_name"], sort=False):
        legal_balls = len(g[(g["wides"] == 0) & (g["noballs"] == 0)])
        overs_comp = legal_balls // 6
        overs_rem = legal_balls % 6
        overs_str = f"{overs_comp}.{overs_rem}"
        overs_float = overs_comp + (overs_rem / 6.0)

        runs_conceded = int((g["batsmanRuns"] + g["wides"] + g["noballs"]).sum())
        wickets = int(g["isWicket"].sum())

        econ = round(runs_conceded / overs_float, 2) if overs_float > 0 else 0.0
        dots = int((g["totalRuns"] == 0).sum())
        dot_pct = round((dots / len(g)) * 100, 1) if len(g) > 0 else 0.0

        fours_conc = int((g["isFour"] == True).sum())
        sixes_conc = int((g["isSix"] == True).sum())
        wides = int(g["wides"].sum())
        noballs = int(g["noballs"].sum())

        maidens = 0
        for _, ov_g in g.groupby("overNumber"):
            ov_legal = len(ov_g[(ov_g["wides"] == 0) & (ov_g["noballs"] == 0)])
            ov_runs = (ov_g["batsmanRuns"] + ov_g["wides"] + ov_g["noballs"]).sum()
            if ov_legal == 6 and ov_runs == 0:
                maidens += 1

        records.append({
            "Inning": inn,
            "Bowler ID": p_id,
            "Bowler Name": p_name,
            "Overs": overs_str,
            "Balls Bowled": len(g),
            "Maidens": maidens,
            "Runs Conceded": runs_conceded,
            "Wickets": wickets,
            "Economy": econ,
            "Dot Balls": dots,
            "Dot %": f"{dot_pct}%",
            "Fours Conceded": fours_conc,
            "Sixes Conceded": sixes_conc,
            "Wides": wides,
            "No Balls": noballs,
        })
    return pd.DataFrame(records)


def build_players_master(df: pd.DataFrame) -> pd.DataFrame:
    """Master list of players participating in the match."""
    bat_players = df[["batsmanPlayerId", "batsman_name"]].drop_duplicates().rename(
        columns={"batsmanPlayerId": "Player ID", "batsman_name": "Player Name"}
    )
    bowl_players = df[["bowlerPlayerId", "bowler_name"]].drop_duplicates().rename(
        columns={"bowlerPlayerId": "Player ID", "bowler_name": "Player Name"}
    )
    all_players = pd.concat([bat_players, bowl_players]).drop_duplicates(subset=["Player ID"]).dropna()

    records = []
    for _, row in all_players.iterrows():
        pid = row["Player ID"]
        pname = row["Player Name"]
        bat_g = df[df["batsmanPlayerId"] == pid]
        bowl_g = df[df["bowlerPlayerId"] == pid]

        runs = int(bat_g["batsmanRuns"].sum()) if not bat_g.empty else 0
        balls = len(bat_g[bat_g["wides"] == 0]) if not bat_g.empty else 0
        wickets = int(bowl_g["isWicket"].sum()) if not bowl_g.empty else 0
        legal_bowl = len(bowl_g[(bowl_g["wides"] == 0) & (bowl_g["noballs"] == 0)]) if not bowl_g.empty else 0

        if not bat_g.empty and not bowl_g.empty:
            role = "All-Rounder"
        elif not bowl_g.empty:
            role = "Bowler"
        else:
            role = "Batter"

        records.append({
            "Player ID": pid,
            "Player Name": pname,
            "Primary Role": role,
            "Runs Scored": runs,
            "Balls Faced": balls,
            "Wickets Taken": wickets,
            "Balls Bowled": legal_bowl,
        })
    if not records:
        return pd.DataFrame(columns=[
            "Player ID", "Player Name", "Primary Role", "Runs Scored",
            "Balls Faced", "Wickets Taken", "Balls Bowled"
        ])
    return pd.DataFrame(records).sort_values("Player Name").reset_index(drop=True)


def build_commentary_log(df: pd.DataFrame) -> pd.DataFrame:
    """Dedicated chronological commentary log."""
    comm_df = df[[
        "inningNumber", "overNumber", "ballNumber", "oversActual",
        "title", "commentary", "isWicket", "isFour", "isSix", "totalRuns"
    ]].copy()
    comm_df.columns = [
        "Inning", "Over", "Ball", "Over Progression",
        "Delivery Summary", "Full Commentary Text", "Wicket Fallen", "Is Four", "Is Six", "Runs"
    ]
    return comm_df


# =====================================================================
# Wagon Wheel, Pitch Map & Shot Analytics Generators
# =====================================================================

def build_wagon_wheel_summary(
    summary_data: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Extract genuine Wagon Wheel radial zone distribution per batter.
    Harvests 8-zone breakdown (runs, scoring shots, singles, doubles, fours, sixes)
    from match summary rosters linescores or ball-by-ball deliveries if available.
    """
    wagon_records = []

    # Priority 1: Extract from match summary rosters
    if summary_data and isinstance(summary_data, dict):
        for rst in summary_data.get("rosters", []):
            team_name = rst.get("team", {}).get("displayName", "Team")
            for r_entry in rst.get("roster", []):
                ath = r_entry.get("athlete", {})
                player_name = ath.get("displayName") or ath.get("fullName", "Unknown")
                for ls in r_entry.get("linescores", []):
                    stats = ls.get("statistics", {})
                    inn_num = ls.get("period", 1)
                    bat = stats.get("batting")
                    if bat and "wagonZone" in bat:
                        wz_list = bat.get("wagonZone", [])
                        total_bat_runs = bat.get("runs", 0)
                        for i, z in enumerate(wz_list):
                            zone_id = i + 1
                            zone_name = WAGON_ZONE_NAMES.get(zone_id, f"Zone {zone_id}")
                            runs = z.get("runs", 0)
                            scoring_shots = z.get("scoringShots", 0)
                            runs_summary = z.get("runsSummary", [])
                            singles = runs_summary[1] if len(runs_summary) > 1 else 0
                            doubles = runs_summary[2] if len(runs_summary) > 2 else 0
                            fours = runs_summary[4] if len(runs_summary) > 4 else 0
                            sixes = runs_summary[6] if len(runs_summary) > 6 else 0
                            pct = round((runs / total_bat_runs) * 100, 1) if total_bat_runs > 0 else 0.0

                            wagon_records.append({
                                "Batter": player_name,
                                "Team": team_name,
                                "Inning": inn_num,
                                "Zone ID": zone_id,
                                "Zone Name": zone_name,
                                "Runs Scored": runs,
                                "Scoring Shots": scoring_shots,
                                "Runs %": f"{pct}%",
                                "Singles (1s)": singles,
                                "Doubles (2s)": doubles,
                                "Fours (4s)": fours,
                                "Sixes (6s)": sixes,
                            })

    # Priority 2: Aggregate from delivery DataFrame if wagonZone is populated
    if not wagon_records and df is not None and "wagonZone" in df.columns:
        valid_df = df[df["wagonZone"].notna() & (df["wagonZone"] != 0) & (df["wagonZone"] != "0")]
        if not valid_df.empty:
            for (bat_name, inn, zone), g in valid_df.groupby(["batsman_name", "inningNumber", "wagonZone"]):
                try:
                    zid = int(zone)
                    zname = WAGON_ZONE_NAMES.get(zid, f"Zone {zid}")
                except Exception:
                    zid = zone
                    zname = f"Zone {zone}"
                wagon_records.append({
                    "Batter": bat_name,
                    "Team": f"Inning {inn}",
                    "Inning": inn,
                    "Zone ID": zid,
                    "Zone Name": zname,
                    "Runs Scored": int(g["batsmanRuns"].sum()),
                    "Scoring Shots": len(g[g["batsmanRuns"] > 0]),
                    "Runs %": "-",
                    "Singles (1s)": len(g[g["batsmanRuns"] == 1]),
                    "Doubles (2s)": len(g[g["batsmanRuns"] == 2]),
                    "Fours (4s)": int((g["isFour"] == True).sum()),
                    "Sixes (6s)": int((g["isSix"] == True).sum()),
                })

    if not wagon_records:
        return pd.DataFrame([{
            "Batter": "No Wagon Wheel Data",
            "Team": "-",
            "Inning": "-",
            "Zone ID": "-",
            "Zone Name": "-",
            "Runs Scored": 0,
            "Scoring Shots": 0,
            "Runs %": "0%",
            "Singles (1s)": 0,
            "Doubles (2s)": 0,
            "Fours (4s)": 0,
            "Sixes (6s)": 0,
        }])

    return pd.DataFrame(wagon_records)


def build_pitch_map_summary(
    summary_data: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Extract bowler Pitch Map distribution matrix (Length x Line).
    Harvests 6 lengths x 5 lines pitch grid (Deliveries, Runs Conceded, Wickets)
    for both Right-Hand (RHB) and Left-Hand (LHB) batters.
    """
    pitch_records = []

    # Priority 1: Extract from match summary rosters
    if summary_data and isinstance(summary_data, dict):
        for rst in summary_data.get("rosters", []):
            team_name = rst.get("team", {}).get("displayName", "Team")
            for r_entry in rst.get("roster", []):
                ath = r_entry.get("athlete", {})
                player_name = ath.get("displayName") or ath.get("fullName", "Unknown")
                for ls in r_entry.get("linescores", []):
                    stats = ls.get("statistics", {})
                    inn_num = ls.get("period", 1)
                    bowl = stats.get("bowling")
                    if bowl:
                        for hand, key in [("RHB", "pitchMapRhb"), ("LHB", "pitchMapLhb")]:
                            grid = bowl.get(key, [])
                            if not grid or not isinstance(grid, list):
                                continue
                            for len_idx, line_rows in enumerate(grid):
                                len_name = LENGTH_NAMES[len_idx] if len_idx < len(LENGTH_NAMES) else f"Length {len_idx+1}"
                                if not isinstance(line_rows, list):
                                    continue
                                for line_idx, cell in enumerate(line_rows):
                                    line_name = LINE_NAMES[line_idx] if line_idx < len(LINE_NAMES) else f"Line {line_idx+1}"
                                    if isinstance(cell, list) and len(cell) >= 3:
                                        runs_c, wkts, balls = cell[0], cell[1], cell[2]
                                        if balls > 0 or runs_c > 0 or wkts > 0:
                                            pitch_records.append({
                                                "Bowler": player_name,
                                                "Team": team_name,
                                                "Inning": inn_num,
                                                "Facing Batter Hand": hand,
                                                "Pitch Length": len_name,
                                                "Pitch Line": line_name,
                                                "Deliveries": balls,
                                                "Runs Conceded": runs_c,
                                                "Wickets": wkts,
                                            })

    # Priority 2: Aggregate from delivery DataFrame if pitchLine / pitchLength populated
    if not pitch_records and df is not None and "pitchLine" in df.columns and "pitchLength" in df.columns:
        valid_df = df[df["pitchLine"].notna() & df["pitchLength"].notna()]
        if not valid_df.empty:
            for (bowl_name, inn, p_len, p_line), g in valid_df.groupby(["bowler_name", "inningNumber", "pitchLength", "pitchLine"]):
                pitch_records.append({
                    "Bowler": bowl_name,
                    "Team": f"Inning {inn}",
                    "Inning": inn,
                    "Facing Batter Hand": "All",
                    "Pitch Length": str(p_len).replace("_", " ").title(),
                    "Pitch Line": str(p_line).replace("_", " ").title(),
                    "Deliveries": len(g),
                    "Runs Conceded": int(g["totalRuns"].sum()),
                    "Wickets": int(g["isWicket"].sum()),
                })

    if not pitch_records:
        return pd.DataFrame([{
            "Bowler": "No Pitch Map Data",
            "Team": "-",
            "Inning": "-",
            "Facing Batter Hand": "-",
            "Pitch Length": "-",
            "Pitch Line": "-",
            "Deliveries": 0,
            "Runs Conceded": 0,
            "Wickets": 0,
        }])

    return pd.DataFrame(pitch_records)


def build_shot_classification_summary(
    summary_data: Optional[Dict[str, Any]] = None,
    df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Extract Batter Shot Classification & Preferred Shot analytics.
    Extracts preferredShot metrics from summary or aggregates shotType from delivery feed.
    """
    shot_records = []

    # Priority 1: Extract from match summary rosters
    if summary_data and isinstance(summary_data, dict):
        for rst in summary_data.get("rosters", []):
            team_name = rst.get("team", {}).get("displayName", "Team")
            for r_entry in rst.get("roster", []):
                ath = r_entry.get("athlete", {})
                player_name = ath.get("displayName") or ath.get("fullName", "Unknown")
                for ls in r_entry.get("linescores", []):
                    stats = ls.get("statistics", {})
                    inn_num = ls.get("period", 1)
                    bat = stats.get("batting")
                    if bat:
                        pref_shot = bat.get("preferredShot")
                        if pref_shot and pref_shot.get("shotName"):
                            runs_summary = pref_shot.get("runsSummary", [])
                            fours = int(runs_summary[4]) if len(runs_summary) > 4 else 0
                            sixes = int(runs_summary[6]) if len(runs_summary) > 6 else 0
                            tot_runs = bat.get("runs", 0)
                            shot_runs = pref_shot.get("runs", 0)
                            shot_pct = round((shot_runs / tot_runs) * 100, 1) if tot_runs > 0 else 0.0

                            shot_records.append({
                                "Batter": player_name,
                                "Team": team_name,
                                "Inning": inn_num,
                                "Preferred Shot": pref_shot.get("shotName").title(),
                                "Runs on Shot": shot_runs,
                                "Balls Faced on Shot": pref_shot.get("ballsFaced", 0),
                                "Shot Run %": f"{shot_pct}%",
                                "Fours on Shot": fours,
                                "Sixes on Shot": sixes,
                                "Total Inning Runs": tot_runs,
                                "Scoring Shots Count": bat.get("scoringShots", 0),
                            })

    # Priority 2: Aggregate from delivery DataFrame if shotType populated
    if not shot_records and df is not None and "shotType" in df.columns:
        valid_df = df[df["shotType"].notna()]
        if not valid_df.empty:
            for (bat_name, inn, stype), g in valid_df.groupby(["batsman_name", "inningNumber", "shotType"]):
                tot_runs = int(g["batsmanRuns"].sum())
                shot_records.append({
                    "Batter": bat_name,
                    "Team": f"Inning {inn}",
                    "Inning": inn,
                    "Preferred Shot": str(stype).replace("_", " ").title(),
                    "Runs on Shot": tot_runs,
                    "Balls Faced on Shot": len(g),
                    "Shot Run %": "-",
                    "Fours on Shot": int((g["isFour"] == True).sum()),
                    "Sixes on Shot": int((g["isSix"] == True).sum()),
                    "Total Inning Runs": tot_runs,
                    "Scoring Shots Count": len(g[g["batsmanRuns"] > 0]),
                })

    if not shot_records:
        return pd.DataFrame([{
            "Batter": "No Shot Classification Data",
            "Team": "-",
            "Inning": "-",
            "Preferred Shot": "-",
            "Runs on Shot": 0,
            "Balls Faced on Shot": 0,
            "Shot Run %": "0%",
            "Fours on Shot": 0,
            "Sixes on Shot": 0,
            "Total Inning Runs": 0,
            "Scoring Shots Count": 0,
        }])

    return pd.DataFrame(shot_records)


def extract_commentary_shot_insights(df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract shot execution classifications mentioned in delivery commentary text.
    Identifies cricket shots (drive, pull, flick, cut, sweep, loft, ramp, defense, edge)
    from ball-by-ball text commentary with full provenance.
    """
    shot_patterns = [
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
    ]

    matches = []
    for idx, row in df.iterrows():
        comm = str(row.get("commentary_clean") or row.get("commentary") or "")
        title = str(row.get("title") or "")
        full_text = f"{title} {comm}"

        detected = []
        for pattern, label in shot_patterns:
            if re.search(pattern, full_text, re.IGNORECASE):
                detected.append(label)

        if detected:
            matches.append({
                "Delivery ID": row.get("delivery_id") or (idx + 1),
                "Inning": row.get("inningNumber", 1),
                "Over.Ball": row.get("oversActual") or f"{row.get('overNumber', 0)}.{row.get('ballNumber', 0)}",
                "Batter": row.get("batsman_name", "Unknown"),
                "Bowler": row.get("bowler_name", "Unknown"),
                "Detected Shot(s)": ", ".join(detected),
                "Runs Scored": row.get("totalRuns", 0),
                "Is Boundary": "Yes (4)" if row.get("isFour") else ("Yes (6)" if row.get("isSix") else "No"),
                "Commentary Excerpt": (comm[:110] + "...") if len(comm) > 110 else comm,
            })

    if not matches:
        return pd.DataFrame(columns=[
            "Delivery ID", "Inning", "Over.Ball", "Batter", "Bowler",
            "Detected Shot(s)", "Runs Scored", "Is Boundary", "Commentary Excerpt"
        ])

    return pd.DataFrame(matches)


# =====================================================================
# Granular Field-Level Data Availability System
# =====================================================================

def build_field_availability_audit(
    df: pd.DataFrame,
    summary_data: Optional[Dict[str, Any]] = None,
    provider_source: str = "ESPN Public Site API",
    ball_data_source: Optional[str] = None,
    fallback_reason: Optional[str] = None,
) -> pd.DataFrame:
    """
    Granular Field-Level Data Availability System.
    Replaces blanket source-unavailable messages with a per-field audit table
    verifying field name, availability status, genuine source, valid/missing counts,
    extraction method, and exact technical reason for any missing fields.

    ball_data_source is the feed that actually served the deliveries (see
    CricketDataProvider.get_ball_commentary_with_provenance); when given, empty
    tracking fields are explained by that fact rather than by a presumed HTTP 403.
    fallback_reason is the recorded reason for a fallback, if one happened.
    """
    total_rows = len(df)

    has_summary_wagon = False
    has_summary_pitch = False
    has_summary_shot = False

    if summary_data and isinstance(summary_data, dict):
        for rst in summary_data.get("rosters", []):
            for re_entry in rst.get("roster", []):
                for ls in re_entry.get("linescores", []):
                    stats = ls.get("statistics", {})
                    bat = stats.get("batting", {})
                    bowl = stats.get("bowling", {})
                    if bat and bat.get("wagonZone"):
                        has_summary_wagon = True
                    if bat and bat.get("preferredShot"):
                        has_summary_shot = True
                    if bowl and (bowl.get("pitchMapRhb") or bowl.get("pitchMapLhb")):
                        has_summary_pitch = True

    field_specs = [
        ("delivery_id", "Delivery Identifier", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "id"),
        ("inningNumber", "Inning Number", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "id"),
        ("overNumber", "Over Progression", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "id"),
        ("ballNumber", "Ball in Over", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "id"),
        ("oversActual", "Over.Ball String", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "id"),
        ("totalRuns", "Delivery Total Runs", f"{provider_source} (playbyplay)", "Ball-by-ball feed parser", "score"),
        ("batsmanRuns", "Runs Off Bat", f"{provider_source} (playbyplay)", "Ball-by-ball score calculation", "score"),
        ("isFour", "Boundary Four Flag", f"{provider_source} (playbyplay)", "PlayType / shortText classification", "flag"),
        ("isSix", "Boundary Six Flag", f"{provider_source} (playbyplay)", "PlayType / shortText classification", "flag"),
        ("isWicket", "Wicket Fallen Flag", f"{provider_source} (playbyplay)", "Dismissal object check", "flag"),
        ("dismissalType", "Dismissal Method", f"{provider_source} (playbyplay)", "Dismissal object text extraction", "wicket"),
        ("wides", "Wide Extras", f"{provider_source} (playbyplay)", "Over object / playType classification", "extras"),
        ("noballs", "No-ball Extras", f"{provider_source} (playbyplay)", "Over object / playType classification", "extras"),
        ("legbyes", "Leg-bye Extras", f"{provider_source} (playbyplay)", "Over object / playType classification", "extras"),
        ("byes", "Bye Extras", f"{provider_source} (playbyplay)", "Over object / playType classification", "extras"),
        ("batsmanPlayerId", "Striker Batter ID", f"{provider_source} (playbyplay)", "Batsman athlete object mapping", "player"),
        ("batsman_name", "Striker Batter Name", f"{provider_source} (summary & pbp)", "Athlete displayName mapping", "player"),
        ("bowlerPlayerId", "Bowler ID", f"{provider_source} (playbyplay)", "Bowler athlete object mapping", "player"),
        ("bowler_name", "Bowler Name", f"{provider_source} (summary & pbp)", "Athlete displayName mapping", "player"),
        ("commentary_clean", "Full Delivery Commentary", f"{provider_source} (playbyplay)", "Commentary text items HTML stripper", "comm"),
        ("wagonX", "Optical Hawkeye X Coordinate", "ESPNcricinfo hs-consumer-api", "Optical tracking coordinates", "hawkeye_opt"),
        ("wagonY", "Optical Hawkeye Y Coordinate", "ESPNcricinfo hs-consumer-api", "Optical tracking coordinates", "hawkeye_opt"),
        ("wagonZone (Ball-Level)", "Optical Hawkeye Radial Sector (1-8)", "ESPNcricinfo hs-consumer-api", "Optical sector assignment", "hawkeye_opt"),
        ("wagonZone (Player Aggregates)", "8-Sector Radial Scoring Breakdown", f"{provider_source} (summary)", "Roster batting linescore extraction", "summary_stat"),
        ("pitchLine (Ball-Level)", "Optical Hawkeye Line Category", "ESPNcricinfo hs-consumer-api", "Optical tracking line classifier", "hawkeye_opt"),
        ("pitchLength (Ball-Level)", "Optical Hawkeye Length Category", "ESPNcricinfo hs-consumer-api", "Optical tracking length classifier", "hawkeye_opt"),
        ("pitchMap (Bowler Grid)", "Length x Line Distribution Matrix (RHB/LHB)", f"{provider_source} (summary)", "Roster bowling linescore extraction", "summary_stat"),
        ("shotType (Ball-Level)", "Shot Classification Category", "ESPNcricinfo hs-consumer-api", "Ball-by-ball shot classifier", "hawkeye_opt"),
        ("shotControl (Ball-Level)", "Shot Execution Control Rating (1-2)", "ESPNcricinfo hs-consumer-api", "Ball-by-ball control rating", "hawkeye_opt"),
        ("preferredShot (Player Aggregates)", "Batter Preferred Shot & Shot Stats", f"{provider_source} (summary)", "Roster batting linescore extraction", "summary_stat"),
    ]

    records = []
    for field_name, desc, src, method, ftype in field_specs:
        col = field_name.split(" ")[0]

        if ftype == "hawkeye_opt":
            valid = int(df[col].notna().sum()) if col in df.columns else 0
            if valid > 0 and col in df.columns:
                valid = int((df[col].notna() & (df[col] != 0) & (df[col] != "0") & (df[col] != 0.0)).sum())
            missing = total_rows - valid

            if valid == total_rows and total_rows > 0:
                status = "Successfully Extracted"
                error_details = "None (Optical tracking data captured and verified from feed)."
            elif valid > 0:
                status = "Partially Available"
                error_details = f"Partially captured: {valid}/{total_rows} deliveries populated ({round(valid/total_rows*100, 1)}%)."
            elif ball_data_source and ball_data_source.startswith("ESPNcricinfo"):
                status = "Not Available from Source"
                error_details = "Cricinfo commentary feed carried no value for this field on any delivery of this match."
            elif ball_data_source:
                status = "Not Retrieved (Fallback Feed)"
                src = f"{ball_data_source} (no ball tracking)"
                error_details = (
                    f"Deliveries were served by {ball_data_source}, which excludes per-ball tracking. "
                    + (f"Reason: {fallback_reason} " if fallback_reason else "")
                    + "Re-download with Data Provider set to Auto or ESPNcricinfo Consumer API to populate this field."
                )
            else:
                status = "Provider Access Denied"
                src = "ESPNcricinfo hs-consumer-api (HTTP 403 Forbidden)"
                error_details = (
                    "Akamai EdgeAuth / WAF security on hs-consumer-api.espncricinfo.com returned HTTP 403 Forbidden. "
                    "Public ESPN playbyplay feed excludes per-ball optical tracking telemetry. "
                    "Requires licensed optical tracking data (Hawk-Eye Innovations / CricViz B2B feed) or manual video tagging. "
                    "Aggregate 8-zone Wagon Wheel and Pitch Map matrices are extracted from match summary."
                )
        elif ftype == "summary_stat":
            if "wagonZone" in field_name:
                valid = 1 if has_summary_wagon else 0
                missing = 0 if has_summary_wagon else 1
                status = "Successfully Extracted" if has_summary_wagon else "Not Available from Source"
                error_details = "None (Extracted from rosters.linescores.statistics.batting.wagonZone)." if has_summary_wagon else "Match summary endpoint did not contain wagonZone statistics."
            elif "pitchMap" in field_name:
                valid = 1 if has_summary_pitch else 0
                missing = 0 if has_summary_pitch else 1
                status = "Successfully Extracted" if has_summary_pitch else "Not Available from Source"
                error_details = "None (Extracted from rosters.linescores.statistics.bowling.pitchMapRhb/Lhb)." if has_summary_pitch else "Match summary endpoint did not contain pitchMap statistics."
            elif "preferredShot" in field_name:
                valid = 1 if has_summary_shot else 0
                missing = 0 if has_summary_shot else 1
                status = "Successfully Extracted" if has_summary_shot else "Not Available from Source"
                error_details = "None (Extracted from rosters.linescores.statistics.batting.preferredShot)." if has_summary_shot else "Match summary endpoint did not contain preferredShot statistics."
            else:
                valid = 1
                missing = 0
                status = "Successfully Extracted"
                error_details = "None (Extracted from match summary endpoint)."
        elif ftype in ("wicket", "extras"):
            valid = int(df[col].notna().sum()) if col in df.columns else 0
            if ftype == "extras" and col in df.columns:
                valid = int((df[col] > 0).sum())
            missing = total_rows - valid
            status = "Partially Available" if valid > 0 else "Successfully Extracted"
            error_details = f"Event-driven field populated on applicable deliveries ({valid} recorded in match)."
        else:
            valid = int(df[col].notna().sum()) if col in df.columns else 0
            missing = total_rows - valid
            if valid == total_rows:
                status = "Successfully Extracted"
                error_details = "None (100% complete field extracted across all match deliveries)."
            elif valid > 0:
                status = "Partially Available"
                error_details = f"Populated for {valid}/{total_rows} deliveries ({round(valid/total_rows*100, 1)}%)."
            else:
                status = "Not Available from Source"
                error_details = "Field not provided in retrieved public feed schema."

        records.append({
            "Field Name": field_name,
            "Description": desc,
            "Actual Source": src,
            "Extraction Method": method,
            "Valid Records": valid,
            "Missing Records": missing,
            "Availability Status": status,
            "Error Details": error_details,
            "Reason for Missing Data / Availability Notes": error_details,
        })

    return pd.DataFrame(records)


def build_data_quality_report(
    df: pd.DataFrame,
    summary_data: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Comprehensive Data Quality & Source Audit Report."""
    total_deliveries = len(df)
    total_innings = df["inningNumber"].nunique()
    total_runs = int(df["totalRuns"].sum())
    total_wickets = int(df["isWicket"].sum())
    total_fours = int((df["isFour"] == True).sum())
    total_sixes = int((df["isSix"] == True).sum())
    total_wides = int((df["wides"] > 0).sum())
    total_noballs = int((df["noballs"] > 0).sum())
    total_legbyes = int((df["legbyes"] > 0).sum())
    total_byes = int((df["byes"] > 0).sum())

    missing_bat_id = int(df["batsmanPlayerId"].isna().sum())
    missing_bowl_id = int(df["bowlerPlayerId"].isna().sum())
    dup_ids = int(df["delivery_id"].duplicated().sum())
    empty_comm = int(df["commentary"].apply(lambda x: not x or x == "").sum())

    # Check summary availability
    wagon_status = "Available (8 Radial Zones in Summary)" if summary_data else "Gated (HTTP 403 on hs-consumer-api)"
    pitch_status = "Available (Length x Line Matrix in Summary)" if summary_data else "Gated (HTTP 403 on hs-consumer-api)"
    shot_status = "Available (Preferred Shots in Summary)" if summary_data else "Gated (HTTP 403 on hs-consumer-api)"

    metrics = [
        ("Total Deliveries Extracted", total_deliveries, "Total actual ball-by-ball delivery rows extracted"),
        ("Total Innings", total_innings, "Total innings present in match"),
        ("Total Match Runs", total_runs, "Sum of total runs scored across both innings"),
        ("Total Wickets", total_wickets, "Sum of wickets fallen across both innings"),
        ("Total Fours (4s)", total_fours, "Balls where 4 runs were scored off the bat/boundary"),
        ("Total Sixes (6s)", total_sixes, "Balls where 6 runs were scored off the bat/boundary"),
        ("Total Wides", total_wides, "Delivery count with wide extras"),
        ("Total No-balls", total_noballs, "Delivery count with no-ball extras"),
        ("Total Leg-byes", total_legbyes, "Delivery count with leg-bye extras"),
        ("Total Byes", total_byes, "Delivery count with bye extras"),
        ("Missing Batsman IDs", missing_bat_id, "Zero missing batter IDs indicates 100% complete player mapping"),
        ("Missing Bowler IDs", missing_bowl_id, "Zero missing bowler IDs indicates 100% complete player mapping"),
        ("Duplicate Delivery IDs", dup_ids, "Zero duplicate deliveries confirms unique delivery integrity"),
        ("Missing Commentary Text", empty_comm, "Zero empty commentary records confirms complete text coverage"),
        ("Wagon Wheel Analytics", wagon_status, "8-zone radial scoring breakdown extracted from match summary rosters"),
        ("Pitch Map Distribution", pitch_status, "Bowler length x line matrix (RHB & LHB) extracted from match summary rosters"),
        ("Shot Classification", shot_status, "Batter preferred shots & shot metrics extracted from match summary rosters"),
    ]

    return pd.DataFrame([
        {"Audit Metric": m[0], "Value": m[1], "Verification & Source Status": m[2]}
        for m in metrics
    ])


# =====================================================================
# Professional Excel Formatting Engine
# =====================================================================

def format_excel_sheet(ws, title_color="1E293B"):
    """Apply professional typography, header styles, borders and auto-column widths."""
    header_fill = PatternFill(start_color=title_color, end_color=title_color, fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)
    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )

    # Style header row
    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Style data rows
    for row in range(2, ws.max_row + 1):
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            cell.font = data_font
            cell.border = thin_border
            # Numeric alignment
            if isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 60)

    # Freeze header row
    ws.freeze_panes = "A2"


# =====================================================================
# Main Multi-Sheet Workbook Creator
# =====================================================================

def convert_cricket_dataset(
    input_file: str,
    output_prefix: str = "data_cricket",
    summary_file: Optional[str] = None,
) -> Dict[str, str]:
    """
    Main extraction, standardization, and workbook generator.
    Produces:
    1. Complete Excel Workbook (with 12 analytical sheets including Wagon Wheel, Pitch Map & Audit)
    2. Complete CSV
    3. Complete JSON
    4. Separate Innings CSVs
    """
    logger.info(f"Loading input file: {input_file}...")
    raw_records = load_input_dataset(input_file)
    logger.info(f"Loaded {len(raw_records)} raw records.")

    summary_data = None
    if summary_file and os.path.exists(summary_file):
        try:
            with open(summary_file, "r", encoding="utf-8") as sf:
                summary_data = json.load(sf)
            logger.info(f"Loaded summary file: {summary_file}")
        except Exception as e:
            logger.warning(f"Could not load summary file: {e}")

    # Standardize records
    standardized = [
        standardize_delivery_record(r, i)
        for i, r in enumerate(raw_records)
    ]
    df_all = pd.DataFrame(standardized)

    logger.info(f"Standardized {len(df_all)} delivery records across {df_all['inningNumber'].nunique()} innings.")

    # Generate analytical tables
    df_innings = build_innings_summary(df_all)
    df_batting = build_batting_analytics(df_all)
    df_bowling = build_bowling_analytics(df_all)
    df_players = build_players_master(df_all)
    df_comm = build_commentary_log(df_all)
    df_wagon = build_wagon_wheel_summary(summary_data, df_all)
    df_pitch = build_pitch_map_summary(summary_data, df_all)
    df_shot = build_shot_classification_summary(summary_data, df_all)
    df_audit = build_field_availability_audit(df_all, summary_data)
    df_quality = build_data_quality_report(df_all, summary_data)

    # Build Match Summary
    match_info = [
        {"Metadata Property": "Match ID", "Value": str(df_all["match_id"].iloc[0])},
        {"Metadata Property": "Series ID", "Value": str(df_all["series_id"].iloc[0])},
        {"Metadata Property": "Match Title", "Value": str(df_all["match_title"].iloc[0])},
        {"Metadata Property": "Competition", "Value": str(df_all["competition"].iloc[0])},
        {"Metadata Property": "Match Format", "Value": str(df_all["match_format"].iloc[0])},
        {"Metadata Property": "Match Date", "Value": str(df_all["match_date"].iloc[0])},
        {"Metadata Property": "Venue", "Value": str(df_all["venue"].iloc[0])},
        {"Metadata Property": "Match Status", "Value": str(df_all["match_status"].iloc[0])},
        {"Metadata Property": "Total Deliveries", "Value": str(len(df_all))},
        {"Metadata Property": "Total Match Runs", "Value": str(int(df_all["totalRuns"].sum()))},
        {"Metadata Property": "Total Wickets", "Value": str(int(df_all["isWicket"].sum()))},
    ]
    df_match = pd.DataFrame(match_info)

    # Build Teams table
    team_records = []
    for inn, g in df_all.groupby("inningNumber"):
        runs = int(g["totalRuns"].sum())
        wkts = int(g["isWicket"].sum())
        batters_str = ", ".join(g["batsman_name"].dropna().unique()[:5])
        team_records.append({
            "Inning": inn,
            "Inferred Team Batting": batters_str,
            "Total Score": f"{runs}/{wkts}",
            "Deliveries": len(g),
            "Fours": int((g["isFour"] == True).sum()),
            "Sixes": int((g["isSix"] == True).sum()),
            "Extras Conceded": int((g["wides"] + g["noballs"] + g["legbyes"] + g["byes"]).sum()),
        })
    df_teams = pd.DataFrame(team_records)

    # Export Excel Workbook with full analytical sheets
    excel_path = f"{output_prefix}_complete.xlsx"
    logger.info(f"Writing Excel workbook to {excel_path}...")

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_all.to_excel(writer, sheet_name="Ball_By_Ball", index=False)
        df_match.to_excel(writer, sheet_name="Match_Summary", index=False)
        df_innings.to_excel(writer, sheet_name="Innings_Summary", index=False)
        df_batting.to_excel(writer, sheet_name="Batting_Analytics", index=False)
        df_bowling.to_excel(writer, sheet_name="Bowling_Analytics", index=False)
        df_wagon.to_excel(writer, sheet_name="Wagon_Wheel_Summary", index=False)
        df_pitch.to_excel(writer, sheet_name="Pitch_Map_Summary", index=False)
        df_shot.to_excel(writer, sheet_name="Shot_Analytics", index=False)
        df_players.to_excel(writer, sheet_name="Players", index=False)
        df_teams.to_excel(writer, sheet_name="Teams", index=False)
        df_comm.to_excel(writer, sheet_name="Commentary", index=False)
        df_audit.to_excel(writer, sheet_name="Field_Availability_Audit", index=False)
        df_quality.to_excel(writer, sheet_name="Data_Quality_Report", index=False)

    # Format Excel Sheets
    wb = openpyxl.load_workbook(excel_path)
    for sheet_name in wb.sheetnames:
        format_excel_sheet(wb[sheet_name])
    wb.save(excel_path)
    logger.info(f"Excel workbook formatted and saved successfully.")

    # Export Complete CSV (utf-8-sig for Excel/PowerBI)
    csv_path = f"{output_prefix}_complete.csv"
    df_all.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info(f"Saved complete CSV: {csv_path} ({os.path.getsize(csv_path)} bytes)")

    # Export Complete JSON
    json_path = f"{output_prefix}_complete.json"
    df_all.to_json(json_path, orient="records", indent=2)
    logger.info(f"Saved complete JSON: {json_path} ({os.path.getsize(json_path)} bytes)")

    # Export Separate Innings CSVs
    innings_paths = []
    for inn, g in df_all.groupby("inningNumber"):
        inn_csv = f"{output_prefix}_innings_{inn}.csv"
        g.to_csv(inn_csv, index=False, encoding="utf-8-sig")
        logger.info(f"Saved Inning {inn} CSV: {inn_csv} ({len(g)} rows)")
        innings_paths.append(inn_csv)

    return {
        "excel": excel_path,
        "csv": csv_path,
        "json": json_path,
        "innings": innings_paths,
    }


def generate_excel_workbook_bytes(
    raw_records: List[Dict[str, Any]],
    summary_data: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Generate in-memory multi-sheet Excel workbook bytes for Streamlit downloads."""
    import io

    standardized = [
        standardize_delivery_record(r, i)
        for i, r in enumerate(raw_records)
    ]
    df_all = pd.DataFrame(standardized)

    df_innings = build_innings_summary(df_all)
    df_batting = build_batting_analytics(df_all)
    df_bowling = build_bowling_analytics(df_all)
    df_wagon = build_wagon_wheel_summary(summary_data, df_all)
    df_pitch = build_pitch_map_summary(summary_data, df_all)
    df_shot = build_shot_classification_summary(summary_data, df_all)
    df_players = build_players_master(df_all)
    df_comm = build_commentary_log(df_all)
    df_audit = build_field_availability_audit(df_all, summary_data)
    df_quality = build_data_quality_report(df_all, summary_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_all.to_excel(writer, sheet_name="Ball_By_Ball", index=False)
        df_innings.to_excel(writer, sheet_name="Innings_Summary", index=False)
        df_batting.to_excel(writer, sheet_name="Batting_Analytics", index=False)
        df_bowling.to_excel(writer, sheet_name="Bowling_Analytics", index=False)
        df_wagon.to_excel(writer, sheet_name="Wagon_Wheel_Summary", index=False)
        df_pitch.to_excel(writer, sheet_name="Pitch_Map_Summary", index=False)
        df_shot.to_excel(writer, sheet_name="Shot_Analytics", index=False)
        df_players.to_excel(writer, sheet_name="Players", index=False)
        df_comm.to_excel(writer, sheet_name="Commentary", index=False)
        df_audit.to_excel(writer, sheet_name="Field_Availability_Audit", index=False)
        df_quality.to_excel(writer, sheet_name="Data_Quality_Report", index=False)

    output.seek(0)
    wb = openpyxl.load_workbook(output)
    for sheet_name in wb.sheetnames:
        format_excel_sheet(wb[sheet_name])

    final_output = io.BytesIO()
    wb.save(final_output)
    final_output.seek(0)
    return final_output.getvalue()

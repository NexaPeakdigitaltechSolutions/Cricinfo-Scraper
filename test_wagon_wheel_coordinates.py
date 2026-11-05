"""
Comprehensive Testing Suite for Wagon Wheel Coordinate Normalization,
Validation, Extraction, and 2D Field Visualization.
"""

import math
import pytest
import pandas as pd
from cricket_data_converter import (
    normalize_shot_coordinates,
    transform_coordinates_for_presentation,
    build_wagon_wheel_plotly_figure,
    standardize_delivery_record,
    WAGON_ZONE_NAMES,
)

# ---------------------------------------------------------------------
# Scenario 1 & 8: Valid X/Y coordinates and Non-boundary shots
# ---------------------------------------------------------------------
def test_valid_flat_coordinates():
    data = {
        "wagonX": 190,
        "wagonY": 180,
        "wagonZone": 4,
        "batsmanRuns": 1,
    }
    norm = normalize_shot_coordinates(data)
    assert norm["isTracked"] is True
    assert norm["wagonX"] == 190
    assert norm["wagonY"] == 180
    assert norm["wagonZone"] == 4
    assert norm["shotCoordinates"]["x"] == 190
    assert norm["shotCoordinates"]["y"] == 180
    assert norm["shotCoordinates"]["coordinateSystem"] == "cricinfo_hawkeye_360"

    pres = transform_coordinates_for_presentation(norm["wagonX"], norm["wagonY"])
    assert pres["distance"] == 10.0  # sqrt((190-180)^2 + (180-180)^2)
    assert pres["is_boundary"] is False  # Infield / non-boundary


# ---------------------------------------------------------------------
# Scenario 2: Nested coordinate objects (e.g. shotCoordinates, shotLocation)
# ---------------------------------------------------------------------
def test_nested_coordinate_objects():
    # Case A: nested dict with shotCoordinates
    data_nested_a = {
        "shotCoordinates": {"x": 226, "y": 2, "coordinateSystem": "custom_opt"},
        "wagonZone": 1,
    }
    norm_a = normalize_shot_coordinates(data_nested_a)
    assert norm_a["isTracked"] is True
    assert norm_a["wagonX"] == 226
    assert norm_a["wagonY"] == 2
    assert norm_a["wagonZone"] == 1
    assert norm_a["shotCoordinates"]["coordinateSystem"] == "custom_opt"

    # Case B: nested shotLocation
    data_nested_b = {
        "shotLocation": {"x": 115.5, "y": 160.2},
        "zone": 7,
    }
    norm_b = normalize_shot_coordinates(data_nested_b)
    assert norm_b["isTracked"] is True
    assert norm_b["wagonX"] == 115.5
    assert norm_b["wagonY"] == 160.2
    assert norm_b["wagonZone"] == 7

    # Case C: list tuple coordinates [x, y]
    data_nested_c = {
        "coordinates": [4, 137],
        "wagonZone": 7,
    }
    norm_c = normalize_shot_coordinates(data_nested_c)
    assert norm_c["isTracked"] is True
    assert norm_c["wagonX"] == 4
    assert norm_c["wagonY"] == 137


# ---------------------------------------------------------------------
# Scenario 3: Missing X/Y coordinates
# ---------------------------------------------------------------------
def test_missing_coordinates():
    data_missing = {
        "wagonX": None,
        "wagonY": None,
        "wagonZone": None,
    }
    norm = normalize_shot_coordinates(data_missing)
    assert norm["isTracked"] is False
    assert norm["wagonX"] is None
    assert norm["wagonY"] is None
    assert norm["shotCoordinates"] is None

    # Untracked default placeholder (0, 0, 0)
    data_untracked = {
        "wagonX": 0,
        "wagonY": 0,
        "wagonZone": 0,
    }
    norm_untracked = normalize_shot_coordinates(data_untracked)
    assert norm_untracked["isTracked"] is False
    assert norm_untracked["wagonX"] is None
    assert norm_untracked["wagonY"] is None
    assert norm_untracked["shotCoordinates"] is None


# ---------------------------------------------------------------------
# Scenario 4: Zero as a valid coordinate on boundary / axis
# ---------------------------------------------------------------------
def test_zero_as_valid_coordinate():
    # Shot to backward point boundary at X=0, Y=137 in zone 7
    data_zero_boundary = {
        "wagonX": 0,
        "wagonY": 137,
        "wagonZone": 7,
    }
    norm = normalize_shot_coordinates(data_zero_boundary)
    assert norm["isTracked"] is True
    assert norm["wagonX"] == 0
    assert norm["wagonY"] == 137
    assert norm["wagonZone"] == 7
    assert norm["shotCoordinates"]["x"] == 0


# ---------------------------------------------------------------------
# Scenario 5 & 6: Right-handed vs Left-handed batter presentation
# ---------------------------------------------------------------------
def test_rhb_and_lhb_orientation():
    # Square drive on off side (X=4, Y=137)
    x, y = 4, 137
    # RHB: Off-side is to the left (X=4)
    pres_rhb = transform_coordinates_for_presentation(x, y, batter_hand="RHB", mirror_lhb=True)
    assert pres_rhb["x_pres"] == 4
    assert pres_rhb["y_pres"] == 137

    # LHB: When presentation mirroring is requested, off-side mirrors to right or consistent side
    pres_lhb = transform_coordinates_for_presentation(x, y, batter_hand="LHB", mirror_lhb=True)
    assert pres_lhb["x_pres"] == 2 * 180 - 4  # 356
    assert pres_lhb["y_pres"] == 137

    # Raw coordinates remain un-mirrored when mirror_lhb=False
    pres_lhb_raw = transform_coordinates_for_presentation(x, y, batter_hand="LHB", mirror_lhb=False)
    assert pres_lhb_raw["x_pres"] == 4


# ---------------------------------------------------------------------
# Scenario 7: Boundary shots (4s and 6s)
# ---------------------------------------------------------------------
def test_boundary_shot_distance():
    # Leg glance boundary (226, 2)
    pres_boundary = transform_coordinates_for_presentation(226, 2)
    # dist = sqrt((226-180)^2 + (2-180)^2) = sqrt(46^2 + (-178)^2) = sqrt(2116 + 31684) = sqrt(33800) ~ 183.8
    assert pres_boundary["is_boundary"] is True
    assert pres_boundary["distance"] > 150.0


# ---------------------------------------------------------------------
# Scenario 9: Historical matches / standardize_delivery_record integration
# ---------------------------------------------------------------------
def test_delivery_record_preserves_coordinates():
    raw_delivery = {
        "_uid": 101,
        "oversActual": 0.4,
        "totalRuns": 4,
        "batsmanRuns": 4,
        "isFour": True,
        "isSix": False,
        "isWicket": False,
        "wagonX": 226,
        "wagonY": 2,
        "wagonZone": 1,
        "pitchLine": "ON_THE_STUMPS",
        "pitchLength": "SHORT_OF_A_GOOD_LENGTH",
        "shotType": "LEG_GLANCE",
        "shotControl": 1,
        "batsman_name": "Devon Conway",
        "bowler_name": "Mohammed Siraj",
        "match_context.id": 1539437,
        "match_context.series_id": 1539421,
    }
    clean = standardize_delivery_record(raw_delivery, 0)
    assert clean["wagonX"] == 226
    assert clean["wagonY"] == 2
    assert clean["wagonZone"] == 1
    assert clean["shotCoordinates"] == {
        "x": 226,
        "y": 2,
        "coordinateSystem": "cricinfo_hawkeye_360",
    }
    assert clean["shotType"] == "LEG_GLANCE"
    assert clean["shotControl"] == 1


# ---------------------------------------------------------------------
# Scenario 10 & 11: 2D Wagon Wheel Plotly Figure Generation & CSV export
# ---------------------------------------------------------------------
def test_plotly_wagon_wheel_generation():
    df = pd.DataFrame([
        {
            "wagonX": 190, "wagonY": 180, "wagonZone": 4, "batsmanRuns": 0, "isFour": False, "isSix": False, "isWicket": False,
            "batsman_name": "Devon Conway", "bowler_name": "Mohammed Siraj", "oversActual": 0.2, "shotType": "DEFENDED", "shotControl": 1
        },
        {
            "wagonX": 226, "wagonY": 2, "wagonZone": 1, "batsmanRuns": 4, "isFour": True, "isSix": False, "isWicket": False,
            "batsman_name": "Devon Conway", "bowler_name": "Mohammed Siraj", "oversActual": 0.4, "shotType": "LEG_GLANCE", "shotControl": 1
        },
        {
            "wagonX": 4, "wagonY": 137, "wagonZone": 7, "batsmanRuns": 4, "isFour": True, "isSix": False, "isWicket": False,
            "batsman_name": "Will Young", "bowler_name": "Arshdeep Singh", "oversActual": 1.1, "shotType": "SQUARE_DRIVE", "shotControl": 1
        },
        {
            "wagonX": None, "wagonY": None, "wagonZone": None, "batsmanRuns": 0, "isFour": False, "isSix": False, "isWicket": False,
            "batsman_name": "Will Young", "bowler_name": "Arshdeep Singh", "oversActual": 1.2, "shotType": None, "shotControl": None
        },
    ])

    fig = build_wagon_wheel_plotly_figure(df, batter_name="All Batters")
    assert fig is not None
    # Traces for lines + markers
    assert len(fig.data) >= 2

    # Verify CSV export parity
    csv_str = df.to_csv(index=False)
    assert "wagonX" in csv_str
    assert "wagonY" in csv_str
    assert "190" in csv_str
    assert "226" in csv_str


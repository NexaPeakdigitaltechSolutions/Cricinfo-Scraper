import json
import os
import pytest
import pandas as pd

from cricket_data_converter import (
    build_wagon_wheel_summary,
    build_pitch_map_summary,
    build_shot_classification_summary,
    build_field_availability_audit,
    extract_commentary_shot_insights,
    generate_excel_workbook_bytes,
    standardize_delivery_record,
    WAGON_ZONE_NAMES,
    LENGTH_NAMES,
    LINE_NAMES,
)

@pytest.fixture
def match_1539437_summary():
    path = "match_1539437_summary.json"
    assert os.path.exists(path), f"Missing {path}"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture
def match_1539437_deliveries():
    path = "match_1539437_pbp.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("commentary", {}).get("items", [])
            return items
    return []

def test_wagon_wheel_extraction(match_1539437_summary):
    wagon_df = build_wagon_wheel_summary(match_1539437_summary)
    assert not wagon_df.empty
    assert len(wagon_df) > 0
    assert "Batter" in wagon_df.columns
    assert "Zone ID" in wagon_df.columns
    assert "Zone Name" in wagon_df.columns
    assert "Runs Scored" in wagon_df.columns
    assert "Scoring Shots" in wagon_df.columns
    
    # Check that zone names match the 8 radial sectors
    expected_zones = set(WAGON_ZONE_NAMES.values())
    extracted_zones = set(wagon_df["Zone Name"].unique())
    assert expected_zones.issubset(extracted_zones)
    
    # Check Will Young's wagon wheel in 1st ODI
    wy_df = wagon_df[wagon_df["Batter"] == "Will Young"]
    assert not wy_df.empty
    assert len(wy_df) == 8
    assert wy_df["Runs Scored"].sum() > 0

def test_pitch_map_extraction(match_1539437_summary):
    pitch_df = build_pitch_map_summary(match_1539437_summary)
    assert not pitch_df.empty
    assert "Bowler" in pitch_df.columns
    assert "Pitch Length" in pitch_df.columns
    assert "Pitch Line" in pitch_df.columns
    assert "Deliveries" in pitch_df.columns
    assert "Runs Conceded" in pitch_df.columns
    assert "Wickets" in pitch_df.columns
    
    # Bowlers from Match 1539437 should be present
    bowlers = pitch_df["Bowler"].unique()
    assert any("Nathan Smith" in b or "Axar Patel" in b or "Arshdeep" in b or "Jamieson" in b for b in bowlers)
    
    # Verify RHB & LHB stances
    stances = pitch_df["Facing Batter Hand"].unique()
    assert "RHB" in stances

def test_shot_classification_extraction(match_1539437_summary):
    shot_df = build_shot_classification_summary(match_1539437_summary)
    assert not shot_df.empty
    assert "Batter" in shot_df.columns
    assert "Preferred Shot" in shot_df.columns
    assert "Runs on Shot" in shot_df.columns
    assert "Balls Faced on Shot" in shot_df.columns
    
    # Preferred shots should have genuine names like Cover Drive, Flick, etc.
    shots = shot_df["Preferred Shot"].dropna().unique()
    assert len(shots) > 0
    assert any("Cover Drive" in s or "Drive" in s or "Flick" in s or "Pull" in s for s in shots)

def test_commentary_shot_insights(match_1539437_deliveries):
    if not match_1539437_deliveries:
        pytest.skip("PBP deliveries not available")
    
    df_raw = pd.DataFrame([
        {
            "delivery_id": it.get("id"),
            "inningNumber": it.get("period", 1),
            "oversActual": it.get("over", {}).get("overs", 0),
            "batsman_name": it.get("batsman", {}).get("athlete", {}).get("displayName"),
            "bowler_name": it.get("bowler", {}).get("athlete", {}).get("displayName"),
            "totalRuns": it.get("scoreValue", 0),
            "isFour": "four" in str(it.get("text", "")).lower(),
            "isSix": "six" in str(it.get("text", "")).lower(),
            "title": it.get("shortText", ""),
            "commentary_clean": it.get("text", ""),
        }
        for it in match_1539437_deliveries
    ])
    
    insights = extract_commentary_shot_insights(df_raw)
    assert not insights.empty
    assert "Detected Shot(s)" in insights.columns
    assert "Commentary Excerpt" in insights.columns

def test_field_availability_audit(match_1539437_summary):
    # Dummy df representing deliveries extracted from ESPN Public API
    df = pd.DataFrame({
        "delivery_id": [1, 2],
        "inningNumber": [1, 1],
        "overNumber": [1, 1],
        "ballNumber": [1, 2],
        "oversActual": [0.1, 0.2],
        "totalRuns": [0, 4],
        "batsmanRuns": [0, 4],
        "isFour": [False, True],
        "isSix": [False, False],
        "isWicket": [False, False],
        "wides": [0, 0],
        "noballs": [0, 0],
        "legbyes": [0, 0],
        "byes": [0, 0],
        "batsman_name": ["Will Young", "Will Young"],
        "bowler_name": ["Arshdeep Singh", "Arshdeep Singh"],
        "commentary_clean": ["ball 1", "ball 2"],
        "wagonX": [None, None],
        "wagonY": [None, None],
        "wagonZone": [None, None],
        "pitchLine": [None, None],
        "pitchLength": [None, None],
        "shotType": [None, None],
        "shotControl": [None, None],
    })
    
    audit_df = build_field_availability_audit(df, match_1539437_summary)
    assert len(audit_df) >= 25
    
    # Check that optical hawkeye fields are marked Provider Access Denied
    wagon_x_row = audit_df[audit_df["Field Name"] == "wagonX"].iloc[0]
    assert wagon_x_row["Availability Status"] == "Provider Access Denied"
    assert "HTTP 403 Forbidden" in wagon_x_row["Reason for Missing Data / Availability Notes"]
    assert wagon_x_row["Valid Records"] == 0
    assert wagon_x_row["Missing Records"] == 2
    
    # Check that summary aggregates are marked Successfully Extracted
    wagon_summary_row = audit_df[audit_df["Field Name"].str.contains("wagonZone (Player Aggregates)", regex=False)].iloc[0]
    assert wagon_summary_row["Availability Status"] == "Successfully Extracted"
    assert "rosters.linescores.statistics.batting.wagonZone" in wagon_summary_row["Reason for Missing Data / Availability Notes"]

def test_excel_workbook_generation(match_1539437_summary):
    dummy_records = [
        {
            "_uid": 1,
            "inningNumber": 1,
            "overNumber": 1,
            "ballNumber": 1,
            "oversActual": 0.1,
            "totalRuns": 0,
            "batsmanRuns": 0,
            "isFour": False,
            "isSix": False,
            "isWicket": False,
            "batsmanPlayerId": 547749,
            "batsman_name": "Will Young",
            "bowlerPlayerId": 1125976,
            "bowler_name": "Arshdeep Singh",
            "title": "Arshdeep to Young, no run",
            "commentary_clean": "length ball around leg stump",
            "match_context.id": 1539437,
            "match_context.series_id": 1539421,
            "match_context.match_title": "New Zealand vs India, 1st ODI",
            "match_context.venue": "Eden Park, Auckland",
        }
    ]
    excel_bytes = generate_excel_workbook_bytes(dummy_records, match_1539437_summary)
    assert isinstance(excel_bytes, bytes)
    assert len(excel_bytes) > 10000


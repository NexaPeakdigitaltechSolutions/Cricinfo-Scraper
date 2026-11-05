import asyncio
from app import fetch_series_matches, batch_extract, process_flat_schema
import pandas as pd

async def validate():
    print("1. Testing fetch_series_matches(1510719, 'auto')...")
    fixtures, provider_used = await fetch_series_matches(1510719, provider_mode="auto")
    print(f"   => Success! Retrieved {len(fixtures)} matches via {provider_used}")
    assert len(fixtures) == 74, f"Expected 74 matches, got {len(fixtures)}"
    
    first = fixtures[0]
    print(f"   => Match 1: ID={first['id']}, Label={first['label']}, Venue={first['venue']}")
    assert first['id'] == 1527674, f"Expected match ID 1527674, got {first['id']}"

    print("\n2. Testing batch_extract for Match 1527674...")
    deliveries = await batch_extract(1510719, [1527674], provider_mode="auto")
    print(f"   => Extracted {len(deliveries)} delivery records.")
    assert len(deliveries) > 0, "No deliveries extracted"

    print("\n3. Testing process_flat_schema & CSV export...")
    df, hierarchy = process_flat_schema(deliveries)
    print(f"   => DataFrame shape: {df.shape}")
    print(f"   => Hierarchy groups: {list(hierarchy.keys())}")
    
    # Export CSV with BOM
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    print(f"   => Generated CSV bytes: {len(csv_bytes)} bytes")
    with open("test_output_1510719.csv", "wb") as f:
        f.write(csv_bytes)
    print("   => Saved test_output_1510719.csv successfully.")

if __name__ == "__main__":
    asyncio.run(validate())


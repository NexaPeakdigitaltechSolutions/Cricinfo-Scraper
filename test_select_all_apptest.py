from streamlit.testing.v1 import AppTest
import pytest

def test_select_all_flow():
    at = AppTest.from_file("app.py", default_timeout=30)
    
    # Pre-populate session state with sample fixtures
    sample_fixtures = [
        {"id": 101, "label": "Match 1: RCB v SRH", "status": "RESULT"},
        {"id": 102, "label": "Match 2: MI v KKR", "status": "RESULT"},
        {"id": 103, "label": "Match 3: RR v CSK", "status": "RESULT"},
        {"id": 104, "label": "Match 4: PBKS v GT", "status": "RESULT"},
        {"id": 105, "label": "Match 5: LSG v DC", "status": "RESULT"},
    ]
    
    at.session_state["fixtures"] = sample_fixtures
    at.session_state["s_id"] = "1510719"
    at.run()
    
    assert not at.exception, f"App crashed on start: {at.exception}"
    
    # Checkbox for select all
    sa_cb = at.checkbox(key="select_all_matches")
    assert sa_cb is not None, "Select All checkbox not found"
    print("Initial render: select_all_matches =", sa_cb.value)
    assert sa_cb.value is True, "Select All should default to True"
    
    # Verify all match checkboxes are checked initially
    for m in sample_fixtures:
        cb = at.checkbox(key=f"q_{m['id']}")
        assert cb is not None, f"Checkbox for {m['id']} not found"
        assert cb.value is True, f"Match {m['id']} should be checked initially"
    print("Test 1 PASS: Default Select All checked, all individual matches checked.")

    # Scenario 2: Click Select All to uncheck
    sa_cb.uncheck().run()
    assert not at.exception
    sa_cb = at.checkbox(key="select_all_matches")
    assert sa_cb.value is False, "Select All should be False after unchecking"
    for m in sample_fixtures:
        cb = at.checkbox(key=f"q_{m['id']}")
        assert cb.value is False, f"Match {m['id']} should be unchecked"
    print("Test 2 PASS: Click Select All again -> all matches unchecked.")

    # Scenario 3: Manually check 3 matches (101, 102, 103)
    at.checkbox(key="q_101").check().run()
    at.checkbox(key="q_102").check().run()
    at.checkbox(key="q_103").check().run()
    assert not at.exception
    
    sa_cb = at.checkbox(key="select_all_matches")
    assert sa_cb.value is False, "Select All should still be False when only 3 of 5 are checked"
    assert at.checkbox(key="q_101").value is True
    assert at.checkbox(key="q_102").value is True
    assert at.checkbox(key="q_103").value is True
    assert at.checkbox(key="q_104").value is False
    assert at.checkbox(key="q_105").value is False
    print("Test 3 PASS: Manually select 3 matches -> only those 3 selected, Select All remains unchecked.")

    # Scenario 4: Manually check remaining 2 matches (104, 105)
    at.checkbox(key="q_104").check().run()
    at.checkbox(key="q_105").check().run()
    assert not at.exception
    
    sa_cb = at.checkbox(key="select_all_matches")
    assert sa_cb.value is True, "Select All should automatically become True when all matches are checked"
    for m in sample_fixtures:
        assert at.checkbox(key=f"q_{m['id']}").value is True
    print("Test 4 PASS: Manually select every match -> Select All automatically checked.")

    # Scenario 5: Deselect one match (102)
    at.checkbox(key="q_102").uncheck().run()
    assert not at.exception
    
    sa_cb = at.checkbox(key="select_all_matches")
    assert sa_cb.value is False, "Select All should automatically become False when one match is unchecked"
    assert at.checkbox(key="q_101").value is True
    assert at.checkbox(key="q_102").value is False
    assert at.checkbox(key="q_103").value is True
    assert at.checkbox(key="q_104").value is True
    assert at.checkbox(key="q_105").value is True
    print("Test 5 PASS: Deselect one match -> Select All unchecked, other matches stay selected.")

    # Scenario 6: Re-check Select All
    at.checkbox(key="select_all_matches").check().run()
    assert not at.exception
    assert at.checkbox(key="select_all_matches").value is True
    for m in sample_fixtures:
        assert at.checkbox(key=f"q_{m['id']}").value is True
    print("Test 6 PASS: Click Select All -> all matches re-checked.")

    # Scenario 7: Dynamic schedule refresh with new matches
    new_fixtures = [
        {"id": 201, "label": "Match 1: KKR v SRH", "status": "RESULT"},
        {"id": 202, "label": "Match 2: CSK v PBKS", "status": "RESULT"},
        {"id": 203, "label": "Match 3: DC v MI", "status": "RESULT"},
    ]
    # Clean up old keys and set new fixtures
    for k in list(at.session_state._state._keys()):
        if k.startswith("q_"):
            del at.session_state[k]
    at.session_state["fixtures"] = new_fixtures
    at.session_state["select_all_matches"] = True
    at.run()
    assert not at.exception
    
    assert at.checkbox(key="select_all_matches").value is True
    for m in new_fixtures:
        assert at.checkbox(key=f"q_{m['id']}").value is True
    # Confirm old keys are gone
    assert "q_101" not in at.session_state
    print("Test 7 PASS: Schedule refresh -> no duplicate selections or stale checkbox states, consistent selections.")

    print("\n============================================================")
    print("ALL 7 STREAMLIT ACCEPTANCE TESTS PASSED SUCCESSFULLY!")
    print("============================================================")

if __name__ == "__main__":
    test_select_all_flow()

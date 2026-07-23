import json
import shutil

from scripts.audit_context_curator_model_calibration import ARM_SPECS, RESULTS_DIR, build_report


def test_frozen_context_curator_matrix_promotes_luna_medium_as_tier3() -> None:
    report = build_report()

    assert report["matrix_balanced"] is True
    assert report["arms"]["gpt_5_5_control_unpinned"]["accepted"] == 6
    assert report["arms"]["luna_medium_prompt_v3"]["accepted"] == 6
    assert report["arms"]["luna_unpinned_original"]["accepted"] == 3
    assert report["arms"]["luna_unpinned_prompt_v2"]["accepted"] == 4
    assert report["arms"]["terra_unpinned_diagnostic"]["accepted"] == 5
    assert report["conclusion"]["promotion_allowed"] is True
    assert report["conclusion"]["selected_model"] == "gpt-5.6-luna"
    assert report["conclusion"]["reasoning_effort"] == "medium"


def test_context_curator_auditor_fails_closed_on_wrong_effort(tmp_path) -> None:
    for spec in ARM_SPECS.values():
        for source in RESULTS_DIR.glob(str(spec["pattern"])):
            shutil.copy2(source, tmp_path / source.name)
    target = next(tmp_path.glob("context-curator-auth-*-medium-v3-seed-1.json"))
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["execution_config"]["reasoning_effort_override"] = "low"
    target.write_text(json.dumps(payload), encoding="utf-8")

    report = build_report(tmp_path)

    assert report["matrix_balanced"] is False
    assert report["conclusion"]["promotion_allowed"] is False

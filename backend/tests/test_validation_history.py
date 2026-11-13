import runpy
from pathlib import Path

import pytest

render = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/build-validation-history.py"))[
    "render"
]


def record(**changes):
    return {
        "id": "PV-run-1",
        "date": "2026-11-13",
        "commit": "test",
        "module": "writing",
        "scenario": "保存",
        "phase": "stability",
        "result": "failed",
        "record": "runs/check.md",
        "fix_of": "",
        **changes,
    }


def test_empty_history_never_implies_passed():
    assert "未执行不等于通过" in render([])


def test_history_escapes_supplied_text_and_retains_fix_chain():
    page = render([record(scenario="</script><img src=x onerror=alert(1)>", fix_of="previous-failure")])
    assert "<img src=x" not in page
    assert "&lt;/script&gt;" in page
    assert "previous-failure" in page
    assert 'href="runs/check.md"' in page


@pytest.mark.parametrize(
    "target",
    ["../secret.md", "/absolute.md", "C:/secret.md", "https://site/file.md", "a\\secret.md", "run.html"],
)
def test_history_rejects_external_or_escaping_links(target):
    with pytest.raises(ValueError):
        render([record(record=target)])


def test_history_rejects_duplicate_identity_and_invalid_result():
    with pytest.raises(ValueError):
        render([record(), record()])
    with pytest.raises(ValueError):
        render([record(result="pretend-success")])

"""Source correction metadata must not require a kind on legacy audit workers."""

import asyncio
from copy import deepcopy
import hashlib

import pytest

from ainovel.db import dumps
from ainovel.plot_audit import LEGACY_VERSION, VERSION, audit_plot
from test_direction_observation_evidence import PromptWorker, SOURCE, TEXT
from test_plot_audit import AUTHOR, TEXT as SHORT_TEXT, Grounded


@pytest.mark.parametrize(
    ("version", "source_kind"),
    [(LEGACY_VERSION, None), (LEGACY_VERSION, "author_plot"), (VERSION, "author_plot")],
)
def test_legacy_audit_worker_without_kind_preserves_report_and_semantic_frames(version, source_kind):
    explicit = PromptWorker(version=version)
    explicit.task["kind"] = "module"
    before = deepcopy(explicit.task)
    expected = asyncio.run(audit_plot(explicit, SOURCE, TEXT, 1, source_kind=source_kind))
    legacy = PromptWorker(version=version)
    original = deepcopy(legacy.task)
    actual = asyncio.run(audit_plot(legacy, SOURCE, TEXT, 1, source_kind=source_kind))
    assert actual == expected
    assert dumps(legacy.frames) == dumps(explicit.frames)
    assert (
        hashlib.sha256(dumps(legacy.frames).encode()).digest()
        == hashlib.sha256(dumps(explicit.frames).encode()).digest()
    )
    assert legacy.task == original and explicit.task == before
    assert all("显式来源解释纠偏元数据" not in frame for frame, _ in legacy.frames)
    assert "source_correction" not in actual


def test_legacy_audit_worker_without_task_preserves_report_and_semantic_frames():
    explicit = Grounded()
    explicit.task = {"kind": "module", "result": {}}
    expected = asyncio.run(audit_plot(explicit, AUTHOR, SHORT_TEXT, 1))
    legacy = Grounded()
    actual = asyncio.run(audit_plot(legacy, AUTHOR, SHORT_TEXT, 1))
    assert actual == expected
    assert dumps(legacy.requests) == dumps(explicit.requests)
    assert not hasattr(legacy, "task")

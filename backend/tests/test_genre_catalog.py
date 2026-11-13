import json
from pathlib import Path
import re

import pytest

from ainovel.genre_catalog import assemble_profile, get_catalog, profile_for_action


def test_runtime_catalog_reuses_all_reviewed_genres_topics_and_channel_variants():
    source = Path(__file__).parents[2] / "novel-ai-modules-review.html"
    design = json.loads(
        re.search(
            r'<script id="module-data" type="application/json">(.*?)</script>',
            source.read_text(encoding="utf-8"),
            re.S,
        ).group(1)
    )
    catalog = get_catalog()
    assert len(catalog["genres"]) == 36
    assert len(catalog["mechanisms"]) == 28
    assert {g["id"] for g in catalog["genres"]} == {g["id"] for g in design["genres"]}
    runtime_topics = [
        *catalog["core_topics"],
        *(topic for genre in catalog["genres"] for topic in genre["topics"]),
        *catalog["mechanisms"],
    ]
    assert len(runtime_topics) == len(design["coverage"]) == 350
    assert sum(len(t["items"]) for t in runtime_topics) == 1788
    assert {t["id"]: t["items"] for t in runtime_topics} == {t["id"]: t["items"] for t in design["coverage"]}
    for genre in catalog["genres"]:
        reviewed = next(v for v in design["audienceVariants"] if v["genre"] == genre["id"])
        for channel in ("male", "female"):
            assert genre["profiles"][channel]["focus"] == reviewed["profiles"][channel]["focus"]
            assert genre["profiles"][channel]["checks"] == reviewed["profiles"][channel]["checks"]
    catalog["genres"].clear()
    assert len(get_catalog()["genres"]) == 36


@pytest.mark.parametrize("channel", ["男频", "女频"])
def test_every_genre_has_distinct_contentful_channel_guidance(channel):
    for genre in get_catalog()["genres"]:
        profile = assemble_profile({"audience": channel, "genres": [genre["id"]]})
        assert profile["primary_genre"] == genre["name"]
        assert profile["genre_ids"] == [genre["id"]]
        assert profile["focus"] and profile["reading_promises"] and profile["checks"]
        assert any(t["group"] == genre["id"] and t["items"] for t in profile["required_topics"])
        assert genre["audit"] in profile["checks"]


def test_primary_secondary_legacy_custom_mechanisms_and_author_exceptions_survive():
    profile = assemble_profile(
        {
            "audience": "女频",
            "genres": ["修仙", "悬疑", "作者自定义题材"],
            "mechanisms": ["无CP", "plugin-2", "自定义表达机制"],
            "text": "交易失败，不发生正面战斗。",
            "author_exceptions": ["无CP", "事业成长优先"],
        }
    )
    assert profile["genre_ids"] == ["xianxia", "mystery"]
    assert profile["primary_genre"] == "仙侠 · 修仙修真"
    assert profile["secondary_genres"] == ["悬疑 · 推理 · 解谜", "作者自定义题材"]
    assert profile["custom_genres"] == ["作者自定义题材"]
    assert profile["mechanism_ids"] == ["plugin-20", "plugin-2"]
    assert profile["custom_mechanisms"] == ["自定义表达机制"]
    assert profile["author_notes"] == "交易失败，不发生正面战斗。"
    assert profile["author_exceptions"] == ["无CP", "事业成长优先"]
    assert profile["priority"][0].startswith("作者明确")
    assert any("不因女频自动增加爱情" in c for c in profile["checks"])
    male = assemble_profile({"audience": "男频", "genres": ["xianxia"]})
    assert male["focus"] != profile["focus"]
    assert any("不默认后宫" in c for c in male["focus"])


def test_incompatible_preferences_are_visible_and_do_not_override_no_cp_contract():
    profile = assemble_profile({"audience": "女频", "genres": ["romance"], "author_exceptions": ["无CP"]})
    assert profile["author_exceptions"] == ["无CP"]
    assert any("保留作者约定" in c and "爱情" in c for c in profile["conflicts"])
    assert "不产生正文事实或新硬约束" in profile["policy"]


def test_missing_profile_does_not_infer_channel_or_relationship_and_keeps_ambiguous_custom():
    profile = assemble_profile(None)
    assert profile["configured"] is False
    assert profile["audience"] == "不限"
    assert profile["primary_genre"] is None
    assert profile["genre_ids"] == profile["mechanism_ids"] == profile["reading_promises"] == []
    ambiguous = assemble_profile({"genres": ["现实主义", "xianxia", "修仙"]})
    assert ambiguous["custom_genres"] == ["现实主义"]
    assert ambiguous["primary_genre"] == "现实主义"
    assert ambiguous["genre_ids"] == ["xianxia"]


@pytest.mark.parametrize(
    "action",
    [
        "writing.advise",
        "writing.expand_plot",
        "writing.rewrite_selection",
        "review.chapter",
        "memory.extract",
        "outline.plan",
        "character.generate",
        "world.generate",
        "ideation.explore",
        "全书滚动规划",
        "根据情节补写正文",
        "按章纲写完整正文",
        "审查正文",
        "用局部替换修订正文",
        "从正文提取事实候选与章节摘要",
        "整书完结检查",
        "unknown future action",
    ],
)
def test_action_context_keeps_provenance_and_selected_book_but_avoids_unselected_catalog(action):
    profile = assemble_profile(
        {"audience": "女频", "genres": ["xianxia", "mystery"], "author_exceptions": ["无CP"]}
    )
    profile.update(source_artifact_id="reader-r3", source_story_revision=12)
    clipped = profile_for_action(profile, action)
    assert clipped["source_artifact_id"] == "reader-r3" and clipped["source_story_revision"] == 12
    assert clipped["author_exceptions"] == ["无CP"]
    assert clipped["genre_ids"] == ["xianxia", "mystery"]
    assert {t["group"] for t in clipped["required_topics"]} <= {"core", "xianxia", "mystery"}
    assert clipped["optional_topics"] == []
    assert len(clipped["required_topics"]) + len(clipped["deferred_topics"]) == len(
        profile["required_topics"] + profile["optional_topics"]
    )
    assert len(json.dumps(clipped, ensure_ascii=False)) < 20_000
    assert profile["optional_topics"]

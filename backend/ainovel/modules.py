"""Small module contracts shared by one-shot commands and the durable workflow."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    label: str
    artifact_kind: str
    chapter_required: bool = False
    version: str = "0.1.0"


MODULES = {
    item.id: item
    for item in (
        ModuleSpec("writing.advise", "卡点诊断与方向", "advice"),
        ModuleSpec("writing.rewrite_selection", "改写选区", "text_patch", True),
        ModuleSpec("writing.expand_plot", "按情节补写整章", "chapter_draft", True),
        ModuleSpec("review.chapter", "本章审稿", "review", True),
        ModuleSpec("memory.extract", "提取故事事实", "memory", True),
        ModuleSpec("story.extract", "按正文整理长篇结构候选", "story_extraction"),
        ModuleSpec("ideation.explore", "创意与定位", "advice", version="0.2.0"),
        ModuleSpec("ideation.structure", "结构完整的小说构思", "advice", version="1.0.0"),
        ModuleSpec("intent.compile", "整理作者情绪、钩子与方向", "intent"),
        ModuleSpec("revision.targeted", "按审稿问题定向修订", "chapter_draft", True),
        ModuleSpec("reference.analyze", "拆解参考写法", "reference_analysis"),
        ModuleSpec("reference.plan", "本书参考应用方案", "reference_plan", True),
        ModuleSpec("reference.draft", "参考方案原创试写", "chapter_draft", True),
        ModuleSpec("reference.check", "参考写法对照检查", "reference_comparison", True),
        ModuleSpec("outline.plan", "大纲规划", "outline"),
        ModuleSpec("character.generate", "人物设计", "characters"),
        ModuleSpec("world.generate", "世界设定", "world"),
    )
}


def public_modules():
    return [asdict(item) for item in MODULES.values()]

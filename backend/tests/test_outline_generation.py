import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from sqlite3 import IntegrityError
from threading import Barrier
from uuid import uuid4

import pytest

from ainovel.db import dumps
from ainovel.project_brief import ConfirmProject, confirm
from ainovel.worker import Worker
from test_outline_workspace import chapter_plan, put_and_confirm, setting_confirmation
from test_reference_flow import SOURCE, reference as reference_fixture, update
from test_reference_sync import card


RESOURCE = {"id": "lamps", "title": "油灯库存", "detail": "库房只余两盏旧灯。", "adoption_state": "adopted"}
reference = reference_fixture


class Model:
    def __init__(self, intervene=None):
        self.requests = []
        self.intervene = intervene

    async def generate(self, account, task_id, system, prompt, max_output, **kwargs):
        instruction = json.loads(prompt.split("\n只返回")[0])["任务"]
        assert instruction["动作"] == "生成四级大纲候选"
        self.requests.append(instruction)
        chapters = instruction["章节目录"]
        promise = instruction["可用伏笔"][0]
        settings = [item["id"] for item in instruction["其他可引用设定"]]
        if self.intervene:
            self.intervene()
        plan = {
            "summary": instruction["已确认立项"]["brief"]["ending"],
            "volumes": [
                {
                    "id": "v1",
                    "title": "遗铃",
                    "summary": "沿着铃声验证规则",
                    "units": [
                        {
                            "id": "u1",
                            "title": "求证",
                            "summary": "先试探，再确认",
                            "chapters": [
                                {**chapter_plan(c["id"], c["title"]), "setting_ids": ["bell", *settings]}
                                for c in chapters
                            ],
                        }
                    ],
                }
            ],
            "promises": [
                {
                    "promise_id": promise["id"],
                    "plant_chapter_id": chapters[0]["id"],
                    "opens_chapter_id": chapters[-1]["id"],
                    "closes_chapter_id": chapters[-1]["id"],
                }
            ],
        }
        return {"text": dumps(plan), "usage": {"input": 1, "output": 1}, "call_id": "offline-outline"}


def prepare(client, domain, story):
    branch = story["branch_id"]
    second = domain.create_chapter(branch, "门内响铃")
    project = confirm(
        domain,
        branch,
        ConfirmProject(
            base_revision=0,
            idempotency_key=uuid4().hex,
            idea="找回师姐的铃。",
            brief={
                "position": "短篇规则悬疑",
                "track": "诡异求证",
                "genre_ids": ["xianxia", "mystery"],
                "audience": "喜欢伏笔回收的读者",
                "distinction": "响铃用于查证而非制敌",
                "length": "两章",
                "ending": "找到师姐并归还铜铃",
            },
        ),
    )
    setting_id = setting_confirmation(
        client, branch, [{"id": "resources", "title": "资源台账", "items": [RESOURCE]}]
    )
    record = client.post(
        f"/api/branches/{branch}/story-records",
        json={
            "base_story_revision": domain.branch(branch)["story_revision"],
            "record": {"kind": "promise", "title": "何处能响", "stages": [{"status": "planned"}]},
        },
    )
    assert record.status_code == 201, record.text
    prompt = {
        "account": "deepseek",
        "base_revision": 0,
        "confirmation_id": setting_id,
        "instruction": "保留改过的结局，并安排铃声的伏笔。",
        "client_request_id": uuid4().hex,
    }
    return f"/api/branches/{branch}/outline-workspace", prompt, project, second


def test_outline_ai_candidate_is_reviewable_and_author_save_is_separate(client, domain, settings, story):
    endpoint, prompt, project, second = prepare(client, domain, story)
    queued = client.post(endpoint + "/generation", json=prompt)
    assert queued.status_code == 202, queued.text
    assert client.post(endpoint + "/generation", json=prompt).json()["id"] == queued.json()["id"]
    provider = Model()
    assert asyncio.run(Worker(settings, provider).run_once())
    completed = client.get(endpoint + "/generation").json()[0]
    assert completed["status"] == "succeeded", completed
    assert len(provider.requests) == 1
    sent = provider.requests[0]
    assert sent["已确认立项"]["id"] == project["id"]
    assert sent["已确认世界设定"][0]["detail"] == "铜铃只在门内响。"
    assert sent["其他可引用设定"] == [
        {
            **{key: RESOURCE[key] for key in ("id", "title", "detail")},
            "category_id": "resources",
            "category_title": "资源台账",
        }
    ]
    assert [item["id"] for item in sent["章节目录"]] == [story["chapter_id"], second["id"]]
    assert client.get(endpoint).json()["draft"] is None
    artifact = client.get(endpoint + "/generation/" + completed["artifact_id"]).json()
    assert artifact["current"] is True and artifact["project_revision"] == project["revision"]
    assert artifact["plan"]["summary"] == "找到师姐并归还铜铃"
    saved = client.put(
        endpoint,
        json={
            "base_revision": 0,
            "confirmation_id": prompt["confirmation_id"],
            "source_generation_id": artifact["id"],
            "plan": {**artifact["plan"], "summary": "作者确认归还铜铃，再进入写作"},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["confirmed"] is None
    result = client.post(endpoint + "/confirm", json={"base_revision": saved.json()["draft"]["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["confirmed"]["plan"]["summary"] == "作者确认归还铜铃，再进入写作"
    chosen = result.json()["confirmed"]
    assert chosen["plan"]["volumes"][0]["units"][0]["chapters"][0]["setting_ids"] == ["bell", "lamps"]
    assert chosen["setting_basis"]["confirmation_id"] == prompt["confirmation_id"]
    assert chosen["setting_basis"]["items"][1] == sent["其他可引用设定"][0]
    assert [item["id"] for item in client.get(endpoint + "/setting-options?scope=world").json()["items"]] == [
        "bell"
    ]
    assert result.json()["promises"][0]["state"] == "planned"


@pytest.mark.parametrize("repair_complete", [False, True], ids=["empty-containers", "two-units"])
def test_generated_outline_requires_nonempty_lists_within_the_frozen_directory(
    client, domain, settings, story, repair_complete
):
    endpoint, prompt, _, second = prepare(client, domain, story)
    assert client.post(endpoint + "/generation", json=prompt).status_code == 202
    chapter_ids = [story["chapter_id"], second["id"]]
    future = "后续可以继续追查铜铃的来历。"
    extra_volumes = [{"id": f"v{index}", "title": "后续故事", "summary": future} for index in range(2, 5)]

    class Candidate(Model):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            schema = kwargs["schema"]
            definitions = schema["$defs"]
            for container, field in (
                (schema, "volumes"),
                (definitions["VolumePlan"], "units"),
                (definitions["UnitPlan"], "chapters"),
            ):
                items = container["properties"][field]
                assert field in container["required"]
                assert (items["minItems"], items["maxItems"]) == (1, 2)
            assert definitions["ChapterPlan"]["properties"]["chapter_id"]["enum"] == chapter_ids
            request = self.requests[-1]
            assert "冻结目录的 2 章" in request["要求"]
            assert "后续故事走向保留在总纲或相关摘要" in request["要求"]
            plan = json.loads(output["text"])
            volume = plan["volumes"][0]
            if len(self.requests) == 1:
                plan["volumes"] = [
                    {key: value for key, value in volume.items() if key != "units"},
                    *extra_volumes,
                ]
            else:
                assert len(request["前次产物"]["volumes"]) == 4
                assert all(entry["units"] == [] for entry in request["前次产物"]["volumes"])
                assert "删除没有章纲的单元和卷" in request["修正要求"]
                assert "每个 chapter_id，各一次" in request["修正要求"]
                [unit] = volume["units"]
                if repair_complete:
                    volume["units"] = [
                        {**unit, "id": f"u{index}", "chapters": [chapter]}
                        for index, chapter in enumerate(unit["chapters"], 1)
                    ]
                    volume["summary"] += future
                else:
                    plan["volumes"] += [
                        {**extra, "units": [{**unit, "id": f"u{extra['id']}", "chapters": []}]}
                        for extra in extra_volumes
                    ]
            return {**output, "text": dumps(plan), "call_id": f"outline-directory-{len(self.requests)}"}

    provider = Candidate()
    assert asyncio.run(Worker(settings, provider).run_once())
    assert len(provider.requests) == 2
    completed = client.get(endpoint + "/generation").json()[0]
    assert client.get(endpoint).json()["draft"] is None
    if repair_complete:
        assert completed["status"] == "succeeded", completed
        artifact = client.get(endpoint + "/generation/" + completed["artifact_id"]).json()
        [volume] = artifact["plan"]["volumes"]
        assert future in volume["summary"]
        assert [[chapter["chapter_id"] for chapter in unit["chapters"]] for unit in volume["units"]] == [
            [identifier] for identifier in chapter_ids
        ]
    else:
        assert completed["status"] == "blocked_runtime" and completed["artifact_id"] is None
        assert "每单元至少一份章纲" in completed["error"]
        saved = client.put(
            endpoint,
            json={
                "base_revision": 0,
                "confirmation_id": prompt["confirmation_id"],
                "plan": {"summary": future, "volumes": extra_volumes},
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["draft"]["plan"]["volumes"][0]["units"] == []


def test_outline_directory_bootstrap_replays_and_freezes_existing_ids(client, domain):
    branch = domain.create_novel("遗铃 · 零章大纲")["branch_id"]
    root = f"/api/branches/{branch}"
    endpoint = root + "/outline-workspace"
    setting = setting_confirmation(client, branch)
    version = client.get(endpoint).json()["story_revision"]
    args = {
        "base_revision": 0,
        "base_story_revision": version,
        "chapter_count": 2,
        "client_request_id": uuid4().hex,
    }
    directory = endpoint + "/chapter-directory"
    created = client.post(directory, json=args)
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["story_revision"] == version + 1
    assert client.post(directory, json=args).json() == result
    assert client.post(directory, json={**args, "chapter_count": 1}).json()["code"] == "idempotency"

    chapters = client.get(root + "/chapters").json()
    assert [(row["id"], row["title"], row["revision"], row["chars"]) for row in chapters] == [
        (key, f"第 {index} 章", 0, 0) for index, key in enumerate(result["chapter_ids"], 1)
    ]
    assert client.get(endpoint + "/generation").json() == []
    base = {
        "base_revision": 0,
        "confirmation_id": setting,
        "account": "deepseek",
        "client_request_id": uuid4().hex,
    }
    accepted = client.post(
        endpoint + "/generation", json={**base, "base_story_revision": result["story_revision"]}
    )
    assert accepted.status_code == 202, accepted.text
    assert (
        client.post(
            endpoint + "/generation", json={**base, "base_story_revision": result["story_revision"]}
        ).json()
        == accepted.json()
    )
    frozen = domain.task(accepted.json()["id"])["result"]["outline_generation"]
    assert [row["id"] for row in frozen["chapters"]] == result["chapter_ids"]

    domain.create_chapter(branch, "另一窗口增加一章")
    outdated = client.post(
        endpoint + "/generation",
        json={**base, "client_request_id": uuid4().hex, "base_story_revision": result["story_revision"]},
    )
    assert outdated.status_code == 409 and outdated.json()["code"] == "outline_directory_changed", (
        outdated.text
    )
    assert len(client.get(endpoint + "/generation").json()) == 1


def test_outline_directory_is_atomic_before_retry_and_during_race(client, domain):
    branch = domain.create_novel("遗铃 · 建章竞争")["branch_id"]
    root = f"/api/branches/{branch}"
    endpoint = root + "/outline-workspace/chapter-directory"
    nonce = uuid4().hex
    args = {"base_revision": 0, "base_story_revision": 0, "chapter_count": 2, "client_request_id": nonce}
    with domain.db.connect(True) as tx:
        tx.execute(
            "CREATE TRIGGER fail_directory_insert BEFORE INSERT ON chapters WHEN NEW.position=2 "
            "BEGIN SELECT RAISE(ABORT,'fixture directory aborted'); END"
        )
    with pytest.raises(IntegrityError, match="fixture directory aborted"):
        client.post(endpoint, json=args)
    assert client.get(root + "/chapters").json() == []
    assert domain.branch(branch)["story_revision"] == 0
    assert domain.db.all("SELECT key FROM receipts WHERE key=?", (nonce,)) == []
    with domain.db.connect(True) as tx:
        tx.execute("DROP TRIGGER fail_directory_insert")

    gate = Barrier(2)

    def create(key):
        gate.wait(timeout=5)
        return client.post(endpoint, json={**args, "client_request_id": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(create, [nonce, uuid4().hex]))
    assert sorted(reply.status_code for reply in replies) == [201, 409]
    winner = next(reply.json() for reply in replies if reply.status_code == 201)
    rejected = next(reply.json() for reply in replies if reply.status_code == 409)
    assert rejected["code"] == "outline_directory_changed"
    assert [row["id"] for row in client.get(root + "/chapters").json()] == winner["chapter_ids"]
    assert domain.branch(branch)["story_revision"] == 1


def test_live_directory_change_preserves_stale_candidate_but_blocks_use(client, domain, settings, story):
    endpoint, prompt, _, _ = prepare(client, domain, story)
    assert client.post(endpoint + "/generation", json=prompt).status_code == 202
    provider = Model(lambda: domain.create_chapter(story["branch_id"], "作者新加第三章"))
    assert asyncio.run(Worker(settings, provider).run_once())
    task = client.get(endpoint + "/generation").json()[0]
    assert task["status"] == "succeeded", task
    artifact = client.get(endpoint + "/generation/" + task["artifact_id"]).json()
    assert artifact["current"] is False
    attempted = client.put(
        endpoint,
        json={
            "base_revision": 0,
            "confirmation_id": prompt["confirmation_id"],
            "source_generation_id": artifact["id"],
            "plan": artifact["plan"],
        },
    )
    assert attempted.status_code == 409, attempted.text
    assert client.get(endpoint).json()["draft"] is None


def test_generated_outline_cannot_declare_or_change_author_required_promise(client, domain, settings, story):
    endpoint, prompt, _, _ = prepare(client, domain, story)
    assert client.post(endpoint + "/generation", json=prompt).status_code == 202

    class Candidate(Model):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            if len(self.requests) == 1:
                plan = json.loads(output["text"])
                plan["promises"][0]["fulfillment"] = "author_required"
                output["text"] = dumps(plan)
            return {**output, "call_id": f"outline-required-{len(self.requests)}"}

    provider = Candidate()
    assert asyncio.run(Worker(settings, provider).run_once())
    finished = client.get(endpoint + "/generation").json()[0]
    assert finished["status"] == "succeeded", finished
    assert len(provider.requests) == 2
    assert "必达承诺须由作者声明" in provider.requests[1]["修正要求"]
    artifact = client.get(endpoint + "/generation/" + finished["artifact_id"]).json()
    plan = artifact["plan"]
    assert "fulfillment" not in plan["promises"][0]
    plan["promises"][0].update(
        fulfillment="author_required",
        opens_chapter_id=story["chapter_id"],
        closes_chapter_id=story["chapter_id"],
    )
    saved = client.put(
        endpoint,
        json={"base_revision": 0, "confirmation_id": prompt["confirmation_id"], "plan": plan},
    )
    assert saved.status_code == 200, saved.text
    confirmed = client.post(
        endpoint + "/confirm",
        json={"base_revision": saved.json()["draft"]["revision"], "acknowledge_required_promises": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    prompt.update(base_revision=confirmed.json()["confirmed"]["revision"], client_request_id=uuid4().hex)
    queued = client.post(endpoint + "/generation", json=prompt)
    assert queued.status_code == 202, queued.text
    assert asyncio.run(Worker(settings, Model()).run_once())
    generated = next(
        row for row in client.get(endpoint + "/generation").json() if row["id"] == queued.json()["id"]
    )
    assert generated["status"] == "succeeded", generated
    artifact = client.get(endpoint + "/generation/" + generated["artifact_id"]).json()
    assert artifact["plan"]["promises"] == plan["promises"]


@pytest.mark.parametrize("can_place", [True, False])
def test_outline_card_merge_enforces_per_chapter_limit(client, domain, settings, story, reference, can_place):
    endpoint, prompt, _, second = prepare(client, domain, story)
    first_id, second_id = story["chapter_id"], second["id"]
    update(
        client,
        domain,
        reference,
        cards=[card("😀渡口", id=f"fixed-{index}", technique=f"旧版手法 {index}") for index in range(8)],
    )
    source = reference["payload"]["source"]
    other = client.post(
        "/api/reference-sessions",
        json={
            **{key: source[key] for key in ("unit_id", "start", "end", "quote")},
            "goal": "有限信息下取得局部进展",
            "branch_id": story["branch_id"],
        },
    )
    assert other.status_code == 201, other.text
    update(client, domain, other.json(), cards=[card("😀渡口", technique="另选手法")])
    options = client.get(endpoint + "/reference-options").json()

    def address(item):
        return {key: item[key] for key in ("session_id", "card_id", "revision", "version_id")}

    old = [address(item) for item in options if item["session_id"] == reference["id"]]
    new = address(next(item for item in options if item["session_id"] == other.json()["id"]))
    assert len(old) == 8
    chosen = {
        "summary": "沿铃声查证。",
        "volumes": [
            {
                "id": "v1",
                "title": "遗铃",
                "summary": "沿铃声查证。",
                "units": [
                    {
                        "id": "u1",
                        "title": "查证",
                        "summary": "先试探，再确认。",
                        "chapters": [
                            {**chapter_plan(first_id, "门外核查"), "reference_cards": old},
                            chapter_plan(second_id, "门内核查"),
                        ],
                    }
                ],
            }
        ],
    }
    previous = put_and_confirm(
        client,
        endpoint,
        {**{key: prompt[key] for key in ("base_revision", "confirmation_id")}, "plan": chosen},
    )["confirmed"]

    class Candidate(Model):
        async def generate(self, *args, **kwargs):
            output = await super().generate(*args, **kwargs)
            request = self.requests[-1]
            assert request["各章可新增手法卡数"] == {first_id: 0, second_id: 8}
            assert [address(item) for item in request["已有章纲引用的手法卡摘要"]] == old
            assert [address(item) for item in request["获准采用的手法卡"]] == [new]
            assert SOURCE not in dumps(request) and "😀渡口" not in dumps(request)
            assert kwargs["schema"]["$defs"]["ChapterPlan"]["properties"]["reference_cards"]["maxItems"] == 1
            current = json.loads(output["text"])
            index = 1 if can_place and len(self.requests) > 1 else 0
            current["volumes"][0]["units"][0]["chapters"][index]["reference_cards"] = [new]
            return {
                **output,
                "text": dumps(current),
                "call_id": f"offline-outline-limit-{len(self.requests)}",
            }

    queued = client.post(
        endpoint + "/generation",
        json={**prompt, "base_revision": previous["revision"], "reference_cards": [new]},
    )
    assert queued.status_code == 202, queued.text
    model = Candidate()
    assert asyncio.run(Worker(settings, model).run_once())
    assert len(model.requests) == 2
    assert "已有 8 张手法卡" in model.requests[1]["修正要求"]
    task = client.get(endpoint + "/generation").json()[0]
    if can_place:
        assert task["status"] == "succeeded", task
        response = client.get(endpoint + "/generation/" + task["artifact_id"])
        assert response.status_code == 200, response.text
        candidate = response.json()
        rows = candidate["plan"]["volumes"][0]["units"][0]["chapters"]
        assert [row["reference_cards"] for row in rows] == [old, [new]]
        adopted = put_and_confirm(
            client,
            endpoint,
            {
                "base_revision": previous["revision"],
                "confirmation_id": prompt["confirmation_id"],
                "source_generation_id": candidate["id"],
                "plan": candidate["plan"],
            },
        )["confirmed"]
        assert [row["reference_cards"] for row in adopted["plan"]["volumes"][0]["units"][0]["chapters"]] == [
            old,
            [new],
        ]
        return

    assert task["status"] == "blocked_runtime" and task["artifact_id"] is None
    assert "请把新增引用安排到有余量的章纲" in task["error"]
    relay_path = f"/api/branches/{story['branch_id']}/seven-stage-runs"
    launch = client.post(
        relay_path,
        json={
            "start_from": "settings",
            "source_id": prompt["confirmation_id"],
            "account": "deepseek",
            "budget_limit": 1,
            "client_request_id": uuid4().hex,
            "auto_confirm_planning": True,
            "reference_cards": [new],
        },
    )
    assert launch.status_code == 202, launch.text
    relay_model = Candidate()
    worker = Worker(settings, relay_model)
    for _ in range(3):
        assert asyncio.run(worker.run_once())
    task = domain.task(launch.json()["id"])
    assert task["status"] == "needs_review" and task["result"]["seven_stage_run"]["stage"] == "outline"
    assert "请把新增引用安排到有余量的章纲" in task["error"]
    assert client.get(endpoint).json()["confirmed"] == previous

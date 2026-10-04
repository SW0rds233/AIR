from __future__ import annotations

"""团队与资料库 HTTP 接口的用例 (合并计划 §9.4 / §13.4)。

固定的是**接口契约**: 只读的投影不触发执行; 资料库路径只回显 basename;
删除库不动用户原文件; 部分失败逐条返回而不是当整体成功。
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from src import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    from src.server import app

    return TestClient(app)


# --------------------------------------------------------------------------
# 团队
# --------------------------------------------------------------------------
def test_team_roles_reports_real_availability(client):
    response = client.get("/api/team/roles")
    assert response.status_code == 200
    roles = {r["agent"]: r for r in response.json()["roles"]}
    # 1 个主控 + 7 类功能子智能体
    assert set(roles) == {"supervisor", "evidence", "modeling", "reasoning",
                          "validation", "writing", "figures", "review"}
    # 能力是"探测过的", 不只是静态声明: 每项都带 available/unavailable 结构
    assert "available" in roles["evidence"]
    assert isinstance(roles["evidence"]["unavailable"], list)
    # 审阅仍是唯一可请求降级的角色, 且不得改结论
    assert roles["review"]["may_request_downgrade"] is True
    assert roles["review"]["may_change_conclusion"] is False


def test_team_projection_is_read_only_for_unknown_run(client):
    response = client.get("/api/team/proj-missing/run-missing")
    assert response.status_code == 200
    body = response.json()
    assert body["tasks"] == []
    assert body["event_seq"] == 0


def test_team_run_rejects_empty_request(client):
    assert client.post("/api/team/run", json={"request": "  "}).status_code == 400


def test_team_run_executes_and_reports_unresolved(client):
    """没有资料范围时也必须给出**诚实**结果, 而不是 500 或伪造成功。"""
    response = client.post("/api/team/run", json={
        "request": "判断参数为 2-(211,15,1) 的设计是否存在",
        "project_id": "proj-api", "source_policy": "user_kb", "max_rounds": 8,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["stop_reason"]
    assert body["brief"]["main_question"]
    assert body["unresolved_report"]["deliverables_requested"]
    # 任务摘要里保留失败原因 (不是空壳)
    assert all("outcome" in t for t in body["tasks"])
    # 交付级别的判据是**门槛**, 不是"角色跑过没有": 状态 completed 必须对应门槛通过,
    # 且交付评估本身要随响应返回 (界面/复核据此解释"为什么是这一级")。
    assert "delivery" in body, body.keys()
    if body["status"] == "completed":
        assert body["delivery"].get("accepted") is True, body["delivery"]


def test_team_run_registers_objects_for_a_usable_library(client, tmp_path):
    from src.kb.identity import build_identity
    from src.kb.schema import LitRecord, Provenance
    from src.kb.store import KBStore

    topic = "kb-api"
    store = KBStore(topic)
    item = {"title": "The Nonexistence of Certain Finite Projective Planes",
            "authors": "X", "year": "2001"}
    store.upsert_document(
        LitRecord(doc_id="doc-1", title=item["title"], authors="X", year="2001",
                  abstract="BRC.", manual_asserted=True, has_fulltext=True,
                  identity=build_identity(item)),
        search_text=("The Nonexistence of Certain Finite Projective Planes design "
                     "block design BIBD combinatorial design Bruck Ryser Chowla "
                     "nonexistence"))
    store.set_identity(["title:thenonexistenceofcertainfiniteprojectiveplanes|x|2001"],
                       "doc-1")
    store.add_provenance("doc-1", [Provenance(origin="manual", detail="x.pdf")])
    store.close()

    response = client.post("/api/team/run", json={
        "request": "解释该机理并写成论文: 参数 2-(211,15,1) 的设计是否存在",
        "project_id": "proj-api2", "source_set_ids": [topic],
        "source_policy": "user_kb", "max_rounds": 20,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["objects"]["evidence"] >= 1
    assert body["objects"]["manuscript"] >= 1
    # 投影接口能读到这批对象
    projection = client.get(f"/api/team/{body['plan']['tasks'][0].get('project_id', 'proj-api2')}"
                            f"/{body['run_id']}")
    assert projection.status_code == 200


# --------------------------------------------------------------------------
# 资料库
# --------------------------------------------------------------------------
def test_library_scan_previews_without_importing(client, tmp_path):
    data = tmp_path / "data"
    paper = data / "papers" / "a.md"
    paper.parent.mkdir(parents=True, exist_ok=True)
    paper.write_text("# 题面\n" * 40, encoding="utf-8")
    outside = tmp_path / "outside" / "b.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("# 越界", encoding="utf-8")

    response = client.post("/api/library/scan", json={
        "paths": [str(data / "papers"), str(outside)], "label": "我的库"})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["ok"] == 1
    assert body["counts"]["denied"] == 1
    assert body["truncated"] is False
    # 未授权路径必须给可执行说明
    assert "DATA_READ_ROOTS" in body["denied"][0]["reason"]
    # 只回显 basename, 不泄漏绝对路径
    blob = json.dumps(body, ensure_ascii=False)
    assert str(tmp_path) not in blob
    # 扫描不写入库 (还没有 kb.sqlite)
    assert not (data / "kb" / "我的库" / "kb.sqlite").exists()


def test_library_import_then_detail_then_delete(client, tmp_path):
    from src.kb.store import topic_dir

    data = tmp_path / "data"
    folder = data / "papers"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theorem.md").write_text("# Theorem\n" * 60, encoding="utf-8")

    scan = client.post("/api/library/scan", json={"paths": [str(folder)]}).json()
    assert scan["counts"]["ok"] == 1

    response = client.post("/api/library/import", json={
        "paths": [str(folder)], "topic": "lib-api", "label": "我的文献",
        "idempotency_key": "batch-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["imported"] == 1
    assert body["ok"] is True

    # 幂等: 同键再导入返回同一结果, 不重复解析
    again = client.post("/api/library/import", json={
        "paths": [str(folder)], "topic": "lib-api", "idempotency_key": "batch-1"})
    assert again.json()["counts"] == body["counts"]

    detail = client.get("/api/library/lib-api")
    assert detail.status_code == 200
    info = detail.json()
    assert info["files"] == 1
    assert info["origin"] == "path_import"
    assert info["hashes"][0]["file_hash"]
    assert str(folder) not in json.dumps(info, ensure_ascii=False)

    removed = client.delete("/api/library/lib-api")
    assert removed.status_code == 200
    assert removed.json()["ok"] is True
    # 用户原文件毫发无损
    assert (folder / "theorem.md").is_file()
    assert (topic_dir("lib-api") / "kb.sqlite").exists()


def test_library_import_requires_a_name(client):
    assert client.post("/api/library/import", json={"paths": []}).status_code == 400


def test_library_delete_unknown_is_reported(client):
    response = client.delete("/api/library/definitely-not-here")
    # 库不存在时解除登记仍返回成功语义 (没有登记可解除), 但必须给出说明
    assert response.status_code in (200, 400)
    body = response.json()
    assert "ok" in body or "detail" in body

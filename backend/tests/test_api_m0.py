"""M0 API smoke tests over the 19828 contract + the tauri invoke bridge."""

import json

from fastapi.testclient import TestClient

from backend.core import project_registry, settings_store
from backend.main import app

client = TestClient(app)


def test_health_always_reachable():
    settings_store.save({"apiConfig": {"enabled": False}})
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["tokenSource"] == "none"
    assert body["agent"]["streamProtocol"] == "sse"
    settings_store.save({"apiConfig": {"enabled": True}})


def test_disabled_api_returns_503():
    settings_store.save({"apiConfig": {"enabled": False}})
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "API server is disabled in Settings → API Server"}
    settings_store.save({"apiConfig": {"enabled": True}})


def test_project_lifecycle(tmp_path):
    resp = client.post(
        "/api/v1/projects/create", json={"name": "api-demo", "path": str(tmp_path)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["id"]
    project_path = body["path"]

    # listed and current
    resp = client.get("/api/v1/projects")
    projects = resp.json()["projects"]
    assert any(p["path"] == project_path for p in projects)
    assert resp.json()["currentProject"]["name"] == "api-demo"

    # files listing via the 19828 contract (camelCase isDir)
    resp = client.get(f"/api/v1/projects/current/files?root=wiki")
    files = resp.json()["files"]
    names = {f["name"] for f in files}
    assert "index.md" in names
    assert {"name", "path", "isDir"} <= set(files[0].keys()) | {"isDir"}

    # file content
    resp = client.get(f"/api/v1/projects/current/files/content?path=wiki/index.md")
    assert resp.json()["content"].startswith("# Wiki Index")


def test_files_content_guards(tmp_path):
    client.post("/api/v1/projects/create", json={"name": "guards", "path": str(tmp_path)})
    # traversal
    resp = client.get("/api/v1/projects/current/files/content?path=../etc/passwd")
    assert resp.status_code == 403
    # non-public path
    resp = client.get("/api/v1/projects/current/files/content?path=.llm-wiki/x.json")
    assert resp.status_code == 403
    # binary content type
    resp = client.get("/api/v1/projects/current/files/content?path=wiki/x.pdf")
    assert resp.status_code in (403, 415)


def test_tauri_invoke_bridge(tmp_path):
    client.post("/api/v1/projects/create", json={"name": "bridge", "path": str(tmp_path)})
    project_path = str(tmp_path / "bridge")

    # list_directory returns dirs-first, snake_case is_dir, absolute paths
    resp = client.post(
        "/api/v1/tauri/invoke",
        json={"command": "list_directory", "args": {"path": f"{project_path}/wiki"}},
    )
    assert resp.status_code == 200
    value = resp.json()["value"]
    assert value[0]["is_dir"] is True
    assert value[0]["path"].startswith(project_path)

    # write_file + read_file round-trip
    target = f"{project_path}/wiki/entities/hello.md"
    resp = client.post(
        "/api/v1/tauri/invoke",
        json={"command": "write_file_atomic", "args": {"path": target, "contents": "# Hi"}},
    )
    assert resp.json()["ok"] is True
    resp = client.post(
        "/api/v1/tauri/invoke",
        json={"command": "read_file", "args": {"path": target}},
    )
    assert resp.json()["value"] == "# Hi"

    # traversal defense on absolute paths
    resp = client.post(
        "/api/v1/tauri/invoke",
        json={"command": "read_file", "args": {"path": "/etc/passwd"}},
    )
    assert resp.status_code == 400
    assert "No open project contains path" in resp.json()["error"]


def test_error_envelope_for_unknown_command():
    resp = client.post("/api/v1/tauri/invoke", json={"command": "nope"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_method_not_allowed():
    resp = client.delete("/api/v1/projects")
    assert resp.status_code == 405

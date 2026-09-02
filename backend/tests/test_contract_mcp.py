"""Contract tests: replay the exact request shapes of the bundled
mcp-server (mcp-server/src/api-client.ts) against the FastAPI app, so
the 19828 contract never drifts from what the MCP client expects."""

import os

from fastapi.testclient import TestClient

from backend.core import settings_store
from backend.main import app

client = TestClient(app)


def _mcp_files(project_id="current", root="wiki", recursive=True, max_files=2000):
    # api-client.ts files(): params root/recursive/maxFiles
    return client.get(
        f"/api/v1/projects/{project_id}/files?root={root}"
        f"&recursive={'true' if recursive else 'false'}&maxFiles={max_files}"
    )


def _mcp_file_content(project_id="current", path="wiki/index.md"):
    return client.get(
        f"/api/v1/projects/{project_id}/files/content?path={path}"
    )


def test_health_shape():
    # api-client.ts health() → GET /api/v1/health, auth:false
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    for key in (
        "status", "version", "authRequired", "authConfigured", "tokenSource",
        "enabled", "mcpEnabled", "allowUnauthenticated", "allowLanAccess",
        "agent",
    ):
        assert key in body, key
    assert body["agent"]["streamProtocol"] == "sse"


def test_projects_and_files_shapes(tmp_path):
    client.post("/api/v1/projects/create", json={"name": "mcp-demo", "path": str(tmp_path)})

    resp = client.get("/api/v1/projects")
    body = resp.json()
    assert isinstance(body["projects"], list)
    project = body["currentProject"]
    assert set(project) == {"id", "name", "path", "current"}
    assert project["current"] is True

    resp = _mcp_files()
    body = resp.json()
    assert body["root"] == "wiki"
    assert body["truncated"] is False
    assert all("isDir" in f for f in body["files"])

    resp = _mcp_file_content(path="wiki/index.md")
    body = resp.json()
    assert body["path"] == "wiki/index.md"
    assert body["content"].startswith("# Wiki Index")


def test_token_auth_paths(tmp_path, monkeypatch):
    """api-client.ts sends Authorization: Bearer <token>; the API also
    accepts X-LLM-Wiki-Token and ?token= (api_server.rs is_token_authorized)."""
    client.post("/api/v1/projects/create", json={"name": "auth-demo", "path": str(tmp_path)})
    settings_store.save({"apiConfig": {"token": "test-token-123", "allowUnauthenticated": False}})

    # no token → 401
    assert client.get("/api/v1/projects").status_code == 401
    # Bearer (the MCP transport)
    assert (
        client.get("/api/v1/projects", headers={"Authorization": "Bearer test-token-123"}).status_code
        == 200
    )
    # X-LLM-Wiki-Token
    assert (
        client.get("/api/v1/projects", headers={"X-LLM-Wiki-Token": "test-token-123"}).status_code
        == 200
    )
    # ?token=
    assert client.get("/api/v1/projects?token=test-token-123").status_code == 200
    # wrong token → 401
    assert (
        client.get("/api/v1/projects", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )

    # env token overrides store token (api_server.rs api_token)
    monkeypatch.setenv("LLM_WIKI_API_TOKEN", "env-token")
    assert client.get("/api/v1/projects?token=env-token").status_code == 200
    assert client.get("/api/v1/projects?token=test-token-123").status_code == 401
    monkeypatch.delenv("LLM_WIKI_API_TOKEN")

    settings_store.save({"apiConfig": {"token": "test-token-123", "allowUnauthenticated": True}})
    assert client.get("/api/v1/projects").status_code == 200

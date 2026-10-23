import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock


@pytest.fixture
def archive_api(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://test:test@localhost/test")
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    routes = importlib.import_module("app.api.routes")
    from app.api.deps import get_actor as router_actor
    from app.crud.crud_auth import ActorContext
    from app.core.database import get_db
    from app.core import storage

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    actor = ActorContext("tenant-a", "actor", "admin", 1)
    app.dependency_overrides[router_actor] = lambda: actor
    app.dependency_overrides[routes.get_actor] = lambda: actor
    app.dependency_overrides[get_db] = lambda: Mock()
    lookup = Mock(return_value=object())
    audit = Mock()
    upload = Mock(return_value={"key": "safe-key", "sha256": "digest", "version_id": None})
    monkeypatch.setattr(routes, "get_incident_by_id", lookup)
    monkeypatch.setattr(routes, "append_audit_log", audit)
    monkeypatch.setattr(storage, "archive_incident", upload)
    monkeypatch.setattr(routes.IncidentLogRead, "model_validate", Mock(
        return_value=Mock(model_dump=Mock(return_value={"message_redacted": "safe"}))))
    return TestClient(app), app, routes, lookup, audit, upload


def test_archive_scopes_and_audits(archive_api):
    client, _, _, lookup, audit, upload = archive_api
    response = client.post("/api/incidents/7/archive")
    assert response.status_code == 200
    assert lookup.call_args.kwargs == {
        "tenant_id": "tenant-a", "incident_id": 7, "include_deleted": False,
    }
    assert upload.call_args.args == ("tenant-a", 7, {"message_redacted": "safe"})
    assert audit.call_args.kwargs["action"] == "INCIDENT_ARCHIVE"


@pytest.mark.parametrize("role", ["viewer", "auditor"])
def test_archive_denies_readonly_roles(archive_api, role):
    client, app, routes, lookup, _, upload = archive_api
    from app.crud.crud_auth import ActorContext
    app.dependency_overrides[routes.get_actor] = lambda: ActorContext("tenant-a", "actor", role, 1)
    assert client.post("/api/incidents/7/archive").status_code == 403
    lookup.assert_not_called()
    upload.assert_not_called()


def test_archive_not_found_never_uploads(archive_api):
    client, _, _, lookup, _, upload = archive_api
    lookup.return_value = None
    assert client.post("/api/incidents/7/archive").status_code == 404
    upload.assert_not_called()


def test_archive_failure_never_reports_success(archive_api):
    client, _, _, _, audit, upload = archive_api
    from app.core.storage import ArchiveUnavailable
    upload.side_effect = ArchiveUnavailable()
    response = client.post("/api/incidents/7/archive")
    assert response.status_code == 503
    audit.assert_not_called()

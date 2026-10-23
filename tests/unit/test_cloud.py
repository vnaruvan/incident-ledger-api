"""Isolated cloud contract tests; run with --confcutdir=tests/unit."""
import json
from unittest.mock import Mock

import httpx
import pytest

from app.core.vault import load_vault_secrets
from app.core import storage


def test_vault_disabled(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    assert load_vault_secrets() == {}


def test_vault_kv_contract(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("VAULT_TOKEN", "private-token")
    monkeypatch.delenv("VAULT_TOKEN_FILE", raising=False)
    def handler(request):
        assert request.url.path == "/v1/secret/data/incident-ledger"
        assert request.headers["X-Vault-Token"] == "private-token"
        return httpx.Response(200, json={"data": {"data": {
            "DATABASE_URL": "postgresql://secret", "UNEXPECTED": "ignored",
        }}})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))
    assert load_vault_secrets() == {"DATABASE_URL": "postgresql://secret"}


def test_vault_fails_closed(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example")
    monkeypatch.setenv("VAULT_TOKEN", "private-token")
    monkeypatch.setattr(httpx, "Client", Mock(side_effect=ValueError("private-token")))
    with pytest.raises(RuntimeError, match="^Unable to load required Vault secrets$"):
        load_vault_secrets()


def test_vault_rejects_plaintext_remote(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "http://vault.example")
    with pytest.raises(RuntimeError, match="HTTPS"):
        load_vault_secrets()


@pytest.mark.parametrize("encryption", ["AES256", "aws:kms"])
def test_encrypted_redacted_tenant_archive(monkeypatch, encryption):
    monkeypatch.setenv("S3_ARCHIVE_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_SSE", encryption)
    monkeypatch.setenv("S3_KMS_KEY_ID", "key-arn")
    client = Mock()
    client.put_object.return_value = {"VersionId": "version-1"}
    monkeypatch.setattr(storage.boto3, "client", Mock(return_value=client))
    first = storage.archive_incident("../tenant-a", 1, {"title": "person@example.com"})
    args = client.put_object.call_args.kwargs
    assert args["ServerSideEncryption"] == encryption
    assert "person@example.com" not in args["Body"].decode()
    assert json.loads(args["Body"])["title"]
    assert args["Metadata"]["sha256"] == first["sha256"]
    assert ".." not in first["key"]
    second = storage.archive_incident("tenant-b", 1, {})
    assert first["key"].split("/")[1] != second["key"].split("/")[1]
    assert first["version_id"] == "version-1"
    if encryption == "aws:kms":
        assert args["SSEKMSKeyId"] == "key-arn"


def test_archive_failure_is_sanitized(monkeypatch):
    monkeypatch.setenv("S3_ARCHIVE_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_SSE", "AES256")
    monkeypatch.setattr(storage.boto3, "client", Mock(side_effect=ValueError("secret")))
    with pytest.raises(storage.ArchiveUnavailable) as error:
        storage.archive_incident("tenant", 1, {})
    assert "secret" not in str(error.value)


def test_archive_disabled(monkeypatch):
    monkeypatch.delenv("S3_ARCHIVE_BUCKET", raising=False)
    with pytest.raises(storage.ArchiveUnavailable):
        storage.archive_incident("tenant", 1, {})

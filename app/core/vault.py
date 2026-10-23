"""Optional startup secrets from Vault KV v2 (also supports Vault Agent tokens)."""
import os
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx


def load_vault_secrets() -> dict[str, str]:
    address = os.getenv("VAULT_ADDR", "").rstrip("/")
    if not address:
        return {}
    parsed = urlparse(address)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise RuntimeError("Vault requires HTTPS except on loopback")
    try:
        token_file = os.getenv("VAULT_TOKEN_FILE")
        token = Path(token_file).read_text().strip() if token_file else os.getenv("VAULT_TOKEN", "")
        if not token:
            raise ValueError("Missing Vault token")
        mount = quote(os.getenv("VAULT_KV_MOUNT", "secret"), safe="")
        path = quote(os.getenv("VAULT_SECRET_PATH", "incident-ledger"), safe="/")
        headers = {"X-Vault-Token": token}
        if os.getenv("VAULT_NAMESPACE"):
            headers["X-Vault-Namespace"] = os.environ["VAULT_NAMESPACE"]
        with httpx.Client(verify=os.getenv("VAULT_CACERT") or True, timeout=5.0) as client:
            response = client.get(f"{address}/v1/{mount}/data/{path}", headers=headers)
            response.raise_for_status()
            data = response.json()["data"]["data"]
        allowed = {"DATABASE_URL", "OPENAI_API_KEY"}
        if not isinstance(data, dict) or any(
            not isinstance(v, str) for k, v in data.items() if k in allowed
        ):
            raise ValueError("Invalid Vault secret")
        return {k: v for k, v in data.items() if k in allowed}
    except Exception:
        # Do not include tokens, URLs, response bodies, or secret values in errors.
        raise RuntimeError("Unable to load required Vault secrets") from None

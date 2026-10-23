# core/config.py

import os
from dotenv import load_dotenv

load_dotenv()
from app.core.vault import load_vault_secrets

_vault_secrets = load_vault_secrets()
EMBEDDINGS_PROVIDER = os.getenv("EMBEDDINGS_PROVIDER", "openai").lower()
DATABASE_URL = _vault_secrets.get("DATABASE_URL", os.getenv("DATABASE_URL", ""))
OPENAI_API_KEY = _vault_secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))

VECTOR_DIM = int(os.getenv("VECTOR_DIM", "1536"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

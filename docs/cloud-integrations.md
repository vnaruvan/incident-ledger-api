# Vault and S3 setup

## Vault secrets

The API reads Vault KV v2 once when importing application configuration, before
creating the database engine. Vault is optional: leave VAULT_ADDR unset for local
environment configuration. When enabled, any fetch or authentication failure
stops startup. Vault values override environment values for DATABASE_URL and
OPENAI_API_KEY only. An OpenAI key is optional for local embeddings.

Using the Vault CLI against your configured server:

```sh
vault kv put -mount=secret incident-ledger DATABASE_URL='postgresql+psycopg2://user:password@db:5432/incident_intel'
```

Give the API a token with only this policy:

```hcl
path "secret/data/incident-ledger" {
  capabilities = ["read"]
}
```

Set VAULT_ADDR and VAULT_TOKEN_FILE to a mounted token file. VAULT_TOKEN is
also supported. Use Vault Agent auto-auth to manage production tokens; the API
does not renew tokens itself. HTTPS certificate verification is mandatory;
VAULT_CACERT supports private CAs. Plain HTTP is accepted only on loopback for
local development. Optional VAULT_NAMESPACE supports namespaced deployments.
Restart the API after secret rotation to rebuild the database connection pool.
This uses static KV secrets, not dynamic database leases or Vault Transit.

## Encrypted incident archives

Set S3_ARCHIVE_BUCKET and AWS_DEFAULT_REGION. Boto3 uses its standard credential
provider chain; use a workload IAM role in production. No AWS keys are stored in
source or fetched into the API from Vault.

```sh
curl -X POST http://localhost:8000/api/incidents/1/archive -H "X-API-Key: $KEY"
```

Only responders and admins can archive incidents in their own tenant. Deleted
and other-tenant incidents return 404. The endpoint uploads an immutable-name
JSON snapshot, then records INCIDENT_ARCHIVE in the existing audit chain. It
returns the object key, SHA-256 digest, and optional S3 version ID. Each call
creates another snapshot. There is no public URL or download endpoint.

The snapshot uses the public incident schema (no raw message or embedding vector)
and recursively redacts string values, including titles, tags and stack traces.
Pattern-based redaction does not guarantee removal of every kind of PII.
Object prefixes use SHA-256 tenant identifiers to avoid path ambiguity.
Tenant authorization is enforced by the API; the shared workload role can write
across tenant prefixes. Use separate roles/buckets for infrastructure-level
tenant isolation if required.

Uploads explicitly request SSE-S3 (S3_SSE=AES256) by default. For a customer KMS
key, set S3_SSE=aws:kms and S3_KMS_KEY_ID. Missing configuration or upload failures
return a generic 503. PostgreSQL remains the source of truth; archiving is an
explicit operation and does not move or delete database data.

Provision a private bucket with Block Public Access enabled, Bucket owner enforced
ownership, versioning, and a lifecycle policy appropriate for your retention needs.
The API does not provision cloud resources. Example minimal workload role policy
(replace YOUR_BUCKET):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "s3:PutObject",
    "Resource": "arn:aws:s3:::YOUR_BUCKET/tenants/*/incidents/*"
  }]
}
```

For KMS, also grant kms:GenerateDataKey on the chosen key in the IAM and key
policies. Enforce TLS with a bucket policy denying s3:* when aws:SecureTransport
is false, and enforce your chosen encryption/key in the bucket policy.

S3 upload and PostgreSQL audit insertion are separate operations. If auditing
fails after an upload, the object can exist even though the request fails. Retry
creates a new object; reconcile such objects operationally. This integration does
not provide an atomic outbox, retention lock, or compliance certification.

## Verification

```sh
python -m pytest tests/unit --confcutdir=tests/unit -q
```

These tests mock Vault HTTP and the S3 client; no credentials, database, or cloud
resources are required. Existing database integration tests require PostgreSQL
with pgvector and DATABASE_URL. Validate actual IAM, Vault policy, and KMS access
in your deployment before relying on the integration.

API references: [Vault KV v2](https://developer.hashicorp.com/vault/api-docs/secret/kv/kv-v2)
and [S3 PutObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html).

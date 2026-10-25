# Infrastructure

Pilot simulation runs locally (see root README): FastAPI + Streamlit + Arize Phoenix processes,
SQLite and local-disk object store under `data/`, FAISS index files.

Target production mapping (Terraform / Helm to be added at G5 for a real deployment):
| Local component | Production equivalent |
|---|---|
| SQLite | Managed PostgreSQL (claims, audit, checkpoints) |
| `data/object_store/` | S3 / Blob with object lock for audit retention |
| FAISS files | Managed vector store or FAISS sidecar rebuilt per policy-wording release |
| OpenRouter | Enterprise AI gateway in [region] (ADR-005) |
| Phoenix | Phoenix / OTel collector -> central observability |
| `APPROVAL_TOKEN_SECRET` | KMS-managed signing key |

# sn-change-intel

Config-driven ingestion of ServiceNow `change_request` and `incident` data into S3 (sprint 1),
the first stage of an AI-native change-risk pipeline (embeddings, retrieval, Claude on Bedrock).

## Pipeline

```
ServiceNow -> extractor -> S3 raw -> doc_builder (+PII mask) -> Titan embeddings (Bedrock)
           -> S3 Vectors index -> Claude agent (Bedrock Converse, tool use) -> assessments/*.json in S3
```

- `src/doc_builder` builds one document per change (with outcome and the incidents it caused) and per incident.
- `src/embedder` embeds new or changed documents only (content-hash manifest in `state/embed_manifest.json`).
- `src/agent` runs Claude with tools: `get_change`, `search_similar_changes`, `get_outcome_stats`,
  `get_incidents_for_ci`, `find_overlapping_changes`, `submit_assessment`. Counts come from tools, never the model.
  Assessments are rejected unless every cited record number was actually retrieved.
- Assessments are written to `s3://<raw bucket>/assessments/<CHG>/<prompt_version>-<timestamp>.json`.

## Layout

- `config/tables/*.yaml` - one file per ServiceNow table (fields, watermark, page size)
- `src/common` - config loading, logging
- `src/extractor` - ServiceNow client, watermark store, S3 writer, Lambda handler
- `infra` - Terraform modules (`ingest`, `ai`), environments (`dev`, `prod`) and the one-time `bootstrap` stack
- `docs/SETUP.md` - setup runbook
- `tests/unit` - unit tests (moto, responses)

## How the extractor works

1. Read the watermark for the table from DynamoDB (default: `INITIAL_WATERMARK`).
2. Query the Table API: `sys_updated_on>=watermark - overlap` ordered by `sys_updated_on`, paginated.
3. Write each page to S3 as JSON Lines under `raw/<table>/dt=YYYY-MM-DD/run=<id>/part-NNNNN.jsonl`.
4. Advance the watermark only after every page is written (at-least-once; dedupe on `sys_id` downstream).

## Adding a table

Copy a YAML in `config/tables/`, change the fields, deploy. The event `{"table": "problem"}` runs it.

## Environment variables

| Name | Purpose |
|---|---|
| `SN_INSTANCE_URL` | e.g. `https://yourco.service-now.com` |
| `SN_SECRET_ARN` | Secrets Manager secret with `username` and `password` |
| `RAW_BUCKET` | S3 bucket for raw data |
| `WATERMARK_TABLE` | DynamoDB table for watermarks |
| `CONFIG_DIR` | defaults to `config/tables` |
| `INITIAL_WATERMARK` | defaults to `2000-01-01 00:00:00` |
| `MAX_RUNTIME_SECONDS` | stop early before Lambda timeout (default 780) |

## Deployment: everything through the pipeline

No resource is created by hand. See `docs/SETUP.md` for the runbook.

```
pull request  -> ci (ruff, pytest, terraform fmt/validate, cfn-lint, shellcheck, trivy, gitleaks)
              -> terraform-plan (exact AWS changes posted as a PR comment, read-only role)
merge to main -> deploy: test -> dev (apply, vector index, ServiceNow secret, smoke test)
              -> prod (waits for reviewer approval, then the same steps)
```

- `infra/bootstrap/bootstrap.yaml` is the one-time stack: state bucket, GitHub OIDC trust, pipeline roles.
- `infra/modules/ingest` and `infra/modules/ai` hold the application infrastructure; `infra/envs/dev` and `infra/envs/prod` are the roots.
- `.github/workflows/`: `ci.yml`, `terraform-plan.yml`, `deploy.yml` (calls `_deploy.yml` per environment), `destroy-dev.yml`.
- `scripts/`: `package.sh` (Lambda zip with dependencies), `ensure_vector_index.sh`, `set_sn_secret.sh`, `smoke_test.sh`.

After the first deploy, invoke the agent with `{"change_number": "CHG0012345"}` or `{"score_upcoming": true}`.

Extra env vars: `VECTOR_BUCKET`, `VECTOR_INDEX`, `BEDROCK_MODEL_ID`, `EMBED_MODEL_ID`.

## Local dev

```
pip install -r requirements-dev.txt
pytest
ruff check .
```

For multi-year backfills run the same handler in ECS Fargate or Glue, not Lambda.

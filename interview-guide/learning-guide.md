# Learning Guide: Enterprise RAG App on AWS

A step-by-step path through the whole project, from concepts to code to deployment. Each step has a goal, what to read, the key ideas, a hands-on exercise, and checkpoint questions. Do the steps in order: each one builds on the previous.

**Time:** about 2 weeks at 2-3 hours a day (see the [suggested schedule](#suggested-schedule) at the end).

**How to use it:** keep the code open next to this guide. When a step says "read", read the named functions, not whole files top to bottom. The unit tests are the best documentation of behaviour: when something is unclear, read its test.

---

## Step 0: Set up your machine

**Goal:** be able to run every test and validate the infrastructure locally, without an AWS account.

1. Install Python 3.12, Terraform 1.9+, and Git.
2. Create a virtual environment and install the test tooling:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install tox
   ```
3. Run all tests (Lambda functions + query service):
   ```bash
   tox
   ```
4. Validate the infrastructure for one environment:
   ```bash
   cd environments/dev && terraform init -backend=false && terraform validate && cd ../..
   ```

**Checkpoint:** both tox environments (`lambdas`, `api`) pass, and `terraform validate` prints `Success`.

---

## Step 1: The big picture

**Goal:** explain in two minutes what the system does and how a question flows through it.

**Read:**
- [README.md](../README.md): features, architecture diagram, repository layout.
- [docs/architecture/production-upgrade.md](../docs/architecture/production-upgrade.md): sizing targets, what changed from the POC, network diagram, query and ingestion sequence diagrams, and the **Implementation notes** table at the end.

**Key ideas:**
- Two paths: the **query path** (Streamlit -> CloudFront -> ALB -> FastAPI on Fargate) and the **ingestion path** (S3 -> SQS -> Lambda -> Aurora).
- Everything runs inside one AWS account and VPC; models are called through Amazon Bedrock with IAM, not API keys.
- Users only get answers from documents their user ID or groups are allowed to read.

**Exercise:** without looking, draw the architecture on paper: every box and every arrow. Compare with the diagram and fix what you missed.

**Checkpoint questions:**
1. What are the latency SLOs and the error-rate SLO?
2. Why does the ALB have no public address?
3. Which components exist only in prod and not in dev/staging, and why?

---

## Step 2: RAG fundamentals, mapped to this code

**Goal:** understand the retrieval-augmented generation building blocks and where each lives.

| Concept | What it means | Where it is in the code |
|---|---|---|
| Chunking | Split documents into ~1,000-character pieces with overlap | `chunk_documents()` in [document_processor.py](../src/document_processor/document_processor.py) |
| Embeddings | Turn text into a vector (1024 numbers) that captures meaning | `embed_documents()` (ingestion) and `embed_query()` in [bedrock.py](../services/query_api/app/bedrock.py) |
| Vector search | Find chunks whose vectors are closest to the question's vector | `HYBRID_SEARCH_SQL` (`vec` part) in [retrieval.py](../services/query_api/app/retrieval.py) |
| Keyword search | Postgres full-text search for exact words | `kw` part of the same SQL |
| Hybrid + RRF | Merge both ranked lists with reciprocal rank fusion | `rrf_score` in the same SQL |
| Reranking | A model re-scores question/chunk pairs; keeps the best 5 | `rerank()` in bedrock.py, used in `retrieve()` |
| Grounded generation | The LLM answers only from the given chunks, citing them | `SYSTEM_PROMPT`, `build_user_message()`, `stream_answer()` in bedrock.py |
| Evaluation | Judge answers for faithfulness and relevancy | [evaluation.py](../services/query_api/app/evaluation.py), [evals/run_eval.py](../evals/run_eval.py) |

**Exercise:** read `HYBRID_SEARCH_SQL` and write down, in plain words, what each CTE (`allowed`, `vec`, `kw`) and the final `SELECT` does. Then compute RRF by hand: a chunk ranked 1st in vector search and 3rd in keyword search, with `RRF_K = 60`, scores `1/61 + 1/63`.

**Checkpoint questions:**
1. Why use both vector and keyword search?
2. Why retrieve 20 candidates but send only 5 to the model?
3. What does `is_weak` decide, and what happens when retrieval is weak?

---

## Step 3: Ingestion, from upload to searchable chunks

**Goal:** follow a document from the user's upload to rows in Aurora.

**Read, in this order:**
1. `create_upload_url()` in [main.py](../services/query_api/app/main.py): validates the file type and the groups to share with, then returns a presigned S3 POST with the access list in the object metadata.
2. `upload_document()` in [rag_ui/app.py](../rag_ui/app.py): how the UI uses that presigned POST.
3. The SQS queue, DLQ and S3 notification in [modules/compute/main.tf](../modules/compute/main.tf).
4. [document_processor.py](../src/document_processor/document_processor.py):
   - `handler()`: processes SQS messages and reports failed ones individually (`batchItemFailures`).
   - `jobs_from_message()`: understands S3 events and connector messages.
   - `process_document()`: downloads the file, builds chunks, embeds them, and replaces chunks and ACL in **one transaction**.
   - `parse_acl()`: the owner can always read their document.

**Key ideas:**
- **Idempotency:** re-processing the same document deletes and re-inserts its chunks, so SQS retries are safe.
- **Partial batch failure:** one bad document doesn't fail the others.
- **Dead-letter queue:** after 3 failed attempts a message moves to the DLQ and an alarm fires.

**Exercise:** run only the ingestion tests and read them:
```bash
python -m pytest -q src/tests/unit/test_document_processor.py
```
Then change `parse_acl()` to drop the owner, run the tests, and see which one catches it. Undo the change.

**Checkpoint questions:**
1. Why does the upload go straight to S3 instead of through the API?
2. Where does the document's access list come from for an upload? For a connector message?
3. What happens if embedding fails halfway through a document?

---

## Step 4: Tables, charts and scanned PDFs

**Goal:** understand how the system finds and handles complex content without knowing it in advance.

**Read** [pdf_analysis.py](../src/document_processor/pdf_analysis.py):
- `triage_page()`: decides per page whether it has tables (`find_tables`), large images, vector-drawn charts (many curves/rects/lines), or is a scan (no text layer plus a full-page image).
- `analyze_pdf()`: phase 1 renders flagged pages sequentially; phase 2 calls Textract and the vision model in parallel; phase 3 assembles elements in page order. It falls back to local table extraction if Textract fails.
- `parse_textract()` and `table_to_markdown()`: Textract blocks to Markdown tables.
- `split_table()`: large tables split by rows with the header repeated.
- `describe_figure()`: GPT-6 Luna describes a cropped chart through Bedrock `Converse`; decorative images return `DECORATIVE` and are skipped.
- `build_chunks()` in document_processor.py: turns elements into typed chunks (`text` / `table` / `figure`).

**Exercise:** run the triage on a real PDF of your own (no AWS needed):
```bash
pip install pdfplumber pypdfium2 pillow
python - <<'EOF'
import sys, pdfplumber
sys.path.insert(0, "src/document_processor")
from pdf_analysis import triage_page
with pdfplumber.open("your-file.pdf") as pdf:
    for i, page in enumerate(pdf.pages):
        t = triage_page(page, i)
        print(f"page {i+1}: tables={len(t.table_bboxes)} figures={len(t.figure_bboxes)} scanned={t.scanned}")
EOF
```
Then read [test_pdf_analysis.py](../src/tests/unit/test_pdf_analysis.py) to see the edge cases (small logos ignored, table grid lines not counted as charts).

**Checkpoint questions:**
1. What are the three `document_analysis_mode` values, and when would you use each?
2. Why is rendering sequential but Textract/vision calls parallel?
3. Why keep a table in one chunk?

---

## Step 5: The database

**Goal:** know the schema, the indexes and why the app connects the way it does.

**Read:**
- [db_init.py](../src/db_init/db_init.py): `create_schema()` builds `documents`, `document_acl`, `chunks`, the **HNSW** vector index and the **GIN** full-text index; `ensure_app_role()` creates the least-privileged `rag_app` role and sets HNSW search defaults on it.
- [modules/database/main.tf](../modules/database/main.tf): Aurora cluster, writer + reader, Serverless v2 in non-prod, RDS Proxy in prod, and the `removed` blocks that detach the old RDS instance safely.
- [db.py](../services/query_api/app/db.py): the query service only reads, through the reader endpoint.

**Key ideas:**
- **HNSW vs IVFFlat:** HNSW needs no training step and handles inserts well.
- **ACL table vs metadata on chunks:** changing who can read a document updates a few rows, not every chunk.
- **Role defaults instead of `SET` per session:** avoids pinning RDS Proxy connections.
- **Reader as standby:** one reader serves search queries and is the failover target (~30 s).

**Exercise:** sketch the three tables with their keys and foreign keys. Then explain what `ON DELETE CASCADE` gives you when a document is deleted.

**Checkpoint questions:**
1. Why does `db_init` connect directly to the writer while the app goes through the proxy?
2. What does `hnsw.iterative_scan` fix when ACL filtering removes many candidates?
3. Why do dev/staging skip RDS Proxy?

---

## Step 6: The query service

**Goal:** trace one question end to end through the FastAPI service.

**Read** [main.py](../services/query_api/app/main.py), class `QueryRun`, method `events()`, line by line, opening each called module:
1. `bedrock.apply_guardrail(question, "INPUT")`: blocked questions stop here.
2. `bedrock.embed_query()`.
3. `cache.lookup()` in [cache.py](../services/query_api/app/cache.py): exact match, then semantic match, both scoped to the user's permissions.
4. `retrieval.retrieve()`: hybrid search and rerank.
5. `web_search.search()` in [web_search.py](../services/query_api/app/web_search.py): only when retrieval is weak and the user allows it.
6. `bedrock.stream_answer()`: GPT-6 Luna via the OpenAI SDK's Bedrock provider; output guardrail checks every ~1,500 characters.
7. `finish()`: runs after the response is sent; emits metrics, writes cache and history, samples evaluation.

Also read `query()` (rate limiting, SSE vs JSON mode), [history.py](../services/query_api/app/history.py) and [telemetry.py](../services/query_api/app/telemetry.py) (EMF metrics, OpenTelemetry tracing).

**Exercise:** run the service tests and read [test_query_api.py](../services/query_api/tests/test_query_api.py) as a specification:
```bash
cd services/query_api && pip install -r requirements-dev.txt && python -m pytest -q tests && cd ../..
```
Then add a test: a question with `web_search: false` and weak retrieval should still return an answer, with `web_search_used` false. (Hint: copy `test_web_search_can_be_disabled_per_request`.)

**Checkpoint questions:**
1. What SSE events does `/query` send, in what order, and what does `replace` mean?
2. Why are cached answers scoped by the full set of principals?
3. Why are endpoints synchronous (`def`) and not `async def`, and how is concurrency still handled?

---

## Step 7: Identity and access control

**Goal:** understand how a person becomes a set of ACL principals.

**Read:**
- [modules/auth/main.tf](../modules/auth/main.tf): Cognito user pool, SAML/OIDC identity provider, the UI client (authorization code flow), and the machine client used by CI evaluation.
- [token_enricher.py](../src/token_enricher/token_enricher.py): copies IdP groups into `cognito:groups` in the access token.
- [auth.py](../services/query_api/app/auth.py): validates the JWT and maps claims to `User.principals`.
- `build_login_url()` and `exchange_code_for_tokens()` in [rag_ui/app.py](../rag_ui/app.py): PKCE login from Streamlit.

**Exercise:** follow a user called Priya in the `finance` group: write down her token claims, her principals (`user:<sub>`, `group:finance`), which `document_acl` rows let her read a document, and which cache scope her answers use.

**Checkpoint questions:**
1. Why is the user ID never taken from the request body?
2. How does the CI evaluation client authenticate, and what can it read?
3. What would break if the IdP renamed its groups claim?

---

## Step 8: The Streamlit UI

**Goal:** understand how the UI talks to the API.

**Read** [rag_ui/app.py](../rag_ui/app.py): `handle_oauth_callback()`, `check_token_refresh()`, `stream_query()` (parses Server-Sent Events), `render_chat_page()`, `render_upload_page()`, `render_history_page()`, `send_feedback()`.

**Exercise:** list every API endpoint the UI calls and which page calls it. Then explain why the PKCE verifier is kept in `st.cache_resource` rather than `st.session_state`.

---

## Step 9: Infrastructure as code

**Goal:** be able to read and change any Terraform module with confidence.

**Read the modules in dependency order:**
1. [modules/vpc](../modules/vpc/main.tf): subnets, NAT per AZ, security groups for each tier, VPC endpoints.
2. [modules/database](../modules/database/main.tf) and [modules/cache](../modules/cache/main.tf).
3. [modules/bedrock](../modules/bedrock/main.tf): the Guardrail.
4. [modules/storage](../modules/storage/main.tf): S3 bucket, DynamoDB metadata and chat history.
5. [modules/compute](../modules/compute/main.tf): SQS, DLQ, Lambdas and their IAM.
6. [modules/auth](../modules/auth/main.tf).
7. [modules/query_service](../modules/query_service/main.tf): ECS cluster, task definition with the ADOT sidecar, ALB, blue/green deployment, autoscaling, CloudFront VPC origin.
8. [modules/monitoring](../modules/monitoring/main.tf): alarms and the dashboard.
9. [environments/prod/main.tf](../environments/prod/main.tf) wires them together; compare the three `terraform.tfvars` files to see environment differences.
10. [bootstrap/main.tf](../bootstrap/main.tf): one-time GitHub OIDC roles and ECR.

**Key ideas:** security groups that only allow the next tier; scheduled scaling + target tracking; `ignore_changes` where AWS manages a value (desired count, listener weights); `moved` and `removed` blocks for safe refactors.

**Exercise:** change `rate_limit_per_minute` in `environments/dev/terraform.tfvars`, run `terraform validate`, and trace where the value ends up (hint: `app_environment` -> container environment -> `config.py`). Then revert.

**Checkpoint questions:**
1. Draw which security group allows traffic from which.
2. Why are there two target groups and two request-count scaling policies?
3. What would `terraform plan` do to the old RDS instance, and why?

---

## Step 10: CI/CD and evaluation

**Goal:** understand how code reaches production and what can stop it.

**Read** [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) job by job:
`determine_environment` -> `code-quality` (tox, Terraform checks, SonarQube gate) -> `build_lambda` + `build_image` -> `terraform-plan` -> `terraform-apply` (db_init, wait for the ECS rollout) -> `integration_tests` -> `golden_set_eval`.

Then read [evals/run_eval.py](../evals/run_eval.py) and [evals/golden_set.jsonl](../evals/golden_set.jsonl).

**Key ideas:** GitHub OIDC instead of access keys; immutable image tags (git SHA); the deploy fails if quality or evaluation thresholds aren't met; blue/green rolls back automatically on a 5xx alarm.

**Exercise:** add three golden-set questions about the fixture documents in [evals/documents](../evals/documents), including one the documents can't answer.

**Checkpoint questions:**
1. What runs on a pull request, and what only on a push?
2. Which two thresholds does the evaluation gate enforce?
3. Where would you look if a deployment rolled back?

---

## Step 11: Observability and operations

**Goal:** know what to look at when something goes wrong.

**Read:** the metric names in [telemetry.py](../services/query_api/app/telemetry.py), the alarms and dashboard in [modules/monitoring/main.tf](../modules/monitoring/main.tf), and the incident runbooks in section 14 of the [interview guide](rag-aws-interview-guide.md).

**Exercise:** for each alarm, write one sentence on what it means and the first thing you'd check.

---

## Step 12: Deploy to a dev account (hands-on)

**Goal:** run the whole system for real.

1. Apply [bootstrap/](../bootstrap/main.tf) with admin credentials and set `AWS_DEPLOY_ROLE_ARN` on the GitHub `dev` environment.
2. Enable Bedrock access to GPT-6 Luna, Titan Text Embeddings V2 and Cohere Rerank 3.5.
3. Push to `develop` and watch every pipeline job.
4. Create a Cognito user in dev (no IdP in dev), fill `rag_ui/.env` from the `ui-env-vars-dev` artifact, run `streamlit run rag_ui/app.py`.
5. Upload a PDF with a table and a chart, wait for `processed`, and ask about a value in the table.
6. Open the CloudWatch dashboard and find your request's latency, tokens and cost.

**Checkpoint:** you can explain every number on the dashboard after your test.

---

## Step 13: Capstone, extend the project

Pick one or more. Each touches several layers, which is the best test of understanding:

| Feature | Layers involved | Hint |
|---|---|---|
| Follow-up questions using chat history | query service, history | Load the last turns by `conversation_id` and rewrite the question into a standalone query before retrieval |
| "Failed" document status | ingestion, UI | Write status `failed` on the last receive attempt (`ApproximateReceiveCount`), show it in "My documents" |
| Cache invalidation on ingestion | ingestion, cache | Bump a corpus version key in Valkey after ingestion; include it in cache keys |
| Word / PowerPoint / Excel ingestion | ingestion | Add loaders in `build_chunks()`; keep tables as Markdown |
| Delete a document everywhere | API, DB, S3, cache | One endpoint: S3 object, `documents` row (cascades), metadata, cache scope |
| Conditional reranking (cost) | retrieval | Skip rerank when the top RRF score is well ahead of the second |

For each: write the tests first, run `tox`, run `terraform validate` if infrastructure changes, and update the README.

---

## Suggested schedule

| Days | Steps | Focus |
|---|---|---|
| 1 | 0, 1 | Setup, big picture |
| 2 | 2 | RAG concepts mapped to code |
| 3-4 | 3, 4 | Ingestion, PDF analysis |
| 5 | 5 | Database |
| 6-7 | 6 | Query service |
| 8 | 7, 8 | Auth, UI |
| 9-10 | 9 | Terraform |
| 11 | 10, 11 | CI/CD, evaluation, observability |
| 12 | 12 | Deploy to dev |
| 13-14 | 13 | Capstone feature |

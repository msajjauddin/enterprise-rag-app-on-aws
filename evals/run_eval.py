"""
Golden-set evaluation gate, run by CI after each deployment.

1. Seeds the fixture documents in evals/documents/ (shared with the 'eval' group only)
   and waits until ingestion has processed them.
2. Asks every golden question through the deployed API as the CI evaluation client.
3. Scores retrieval (expected source cited) and answer correctness (GPT judge on
   Bedrock against the reference answer).
4. Exits non-zero when a score is below its threshold, which fails the pipeline.

Usage (CI sets these from Terraform outputs):
    API_ENDPOINT=... EVAL_CLIENT_SECRET_ARN=... DOCUMENT_BUCKET=... METADATA_TABLE=... \
    AWS_REGION=us-east-1 python evals/run_eval.py
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import boto3
import requests
from openai import OpenAI
from openai.providers import bedrock as bedrock_provider

HERE = Path(__file__).parent
API_ENDPOINT = os.environ["API_ENDPOINT"].rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SECRET_ARN = os.environ["EVAL_CLIENT_SECRET_ARN"]
DOCUMENT_BUCKET = os.environ["DOCUMENT_BUCKET"]
METADATA_TABLE = os.environ["METADATA_TABLE"]
JUDGE_MODEL_ID = os.environ.get("JUDGE_MODEL_ID", "us.openai.gpt-6-luna")
MIN_RETRIEVAL_HIT_RATE = float(os.environ.get("MIN_RETRIEVAL_HIT_RATE", "0.9"))
MIN_CORRECTNESS = float(os.environ.get("MIN_CORRECTNESS", "0.8"))
INGESTION_TIMEOUT_S = int(os.environ.get("INGESTION_TIMEOUT_S", "600"))
REPORT_PATH = os.environ.get("EVAL_REPORT", "eval-report.json")

EVAL_OWNER = "service-eval"
EVAL_ACL = "group:eval"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "correctness": {"type": "number", "description": "0-1 agreement with the reference answer"},
        "reason": {"type": "string"},
    },
    "required": ["correctness", "reason"],
    "additionalProperties": False,
}

JUDGE_PROMPT = """Grade an assistant's answer against a reference answer.

<question>{question}</question>
<reference>{reference}</reference>
<answer>{answer}</answer>

correctness is 1 when the answer states the same facts as the reference (wording may differ),
0.5 when it is partly right or misses part of the reference, and 0 when it is wrong or makes
up information the reference says is unavailable. Extra correct detail is fine."""


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def get_token() -> str:
    secret = json.loads(boto3.client("secretsmanager", region_name=AWS_REGION)
                        .get_secret_value(SecretId=SECRET_ARN)["SecretString"])
    response = requests.post(
        secret["token_url"],
        data={"grant_type": "client_credentials", "scope": secret["scope"]},
        auth=(secret["client_id"], secret["client_secret"]),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def seed_documents() -> None:
    """Upload fixtures that changed and wait for ingestion to mark them processed."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(METADATA_TABLE)
    pending = []

    for path in sorted((HERE / "documents").iterdir()):
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()[:16]
        document_id = f"eval-{path.stem}-{digest}"
        item = table.get_item(Key={"id": f"doc#{document_id}"}).get("Item")
        if item and item.get("status") == "processed":
            continue
        key = f"uploads/{EVAL_OWNER}/{document_id}/{path.name}"
        s3.put_object(Bucket=DOCUMENT_BUCKET, Key=key, Body=content, ContentType="text/markdown",
                      Metadata={"acl": EVAL_ACL})
        log(f"Uploaded {path.name} as {document_id}")
        pending.append(document_id)

    deadline = time.time() + INGESTION_TIMEOUT_S
    while pending and time.time() < deadline:
        time.sleep(10)
        pending = [d for d in pending
                   if (table.get_item(Key={"id": f"doc#{d}"}).get("Item") or {}).get("status") != "processed"]
    if pending:
        raise SystemExit(f"Ingestion did not finish for: {pending}")


def ask(token: str, question: str) -> dict:
    for attempt in range(3):
        response = requests.post(
            f"{API_ENDPOINT}/query",
            json={"question": question, "stream": False, "web_search": False},
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", "60")))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("rate limited three times")


def judge(client: OpenAI, question: str, reference: str, answer: str) -> dict:
    response = client.responses.create(
        model=JUDGE_MODEL_ID,
        input=JUDGE_PROMPT.format(question=question, reference=reference, answer=answer),
        reasoning={"effort": "low"},
        max_output_tokens=4000,
        text={"format": {"type": "json_schema", "name": "grade", "schema": JUDGE_SCHEMA, "strict": True}},
        store=False,
    )
    if response.status != "completed" or not response.output_text:
        return {"correctness": 0.0, "reason": f"judge did not complete ({response.status})"}
    return json.loads(response.output_text)


def main() -> int:
    golden = [json.loads(line) for line in (HERE / "golden_set.jsonl").read_text().splitlines() if line.strip()]
    log(f"Seeding fixtures, then running {len(golden)} golden questions against {API_ENDPOINT}")
    seed_documents()

    token = get_token()
    llm = OpenAI(provider=bedrock_provider(region=AWS_REGION, endpoint="runtime", api_key=None))
    results = []

    for case in golden:
        reply = ask(token, case["question"])
        cited = {s["file_name"] for s in reply.get("sources", [])}
        expected = set(case["expected_sources"])
        retrieval_hit = expected <= cited if expected else None
        grade = judge(llm, case["question"], case["reference"], reply["answer"])
        results.append({
            "id": case["id"],
            "retrieval_hit": retrieval_hit,
            "correctness": float(grade["correctness"]),
            "reason": grade["reason"],
            "latency_ms": reply.get("latency_ms"),
            "answer": reply["answer"],
        })
        log(f"{case['id']}: retrieval={retrieval_hit} correctness={grade['correctness']}")

    retrieval_cases = [r for r in results if r["retrieval_hit"] is not None]
    hit_rate = sum(r["retrieval_hit"] for r in retrieval_cases) / max(len(retrieval_cases), 1)
    correctness = sum(r["correctness"] for r in results) / len(results)
    passed = hit_rate >= MIN_RETRIEVAL_HIT_RATE and correctness >= MIN_CORRECTNESS

    report = {
        "passed": passed,
        "retrieval_hit_rate": round(hit_rate, 3),
        "mean_correctness": round(correctness, 3),
        "thresholds": {"retrieval_hit_rate": MIN_RETRIEVAL_HIT_RATE, "mean_correctness": MIN_CORRECTNESS},
        "results": results,
    }
    Path(REPORT_PATH).write_text(json.dumps(report, indent=2))
    log(f"Retrieval hit rate {hit_rate:.2f} (min {MIN_RETRIEVAL_HIT_RATE}), "
        f"mean correctness {correctness:.2f} (min {MIN_CORRECTNESS}) -> {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

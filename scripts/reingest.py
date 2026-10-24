"""
Re-ingest every uploaded document by publishing S3-event-shaped messages to the
ingestion queue. Needed after the embedding model changes (vectors don't carry over).

    python scripts/reingest.py --bucket <document_bucket> --queue-url <ingestion_queue_url> [--prefix uploads/]
"""
import argparse
import json
import urllib.parse

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--prefix", default="uploads/")
    args = parser.parse_args()

    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    batch, sent = [], 0

    def flush():
        nonlocal batch, sent
        if batch:
            response = sqs.send_message_batch(QueueUrl=args.queue_url, Entries=batch)
            for failure in response.get("Failed", []):
                print(f"Failed to enqueue entry {failure['Id']}: {failure.get('Message')}")
            sent += len(batch) - len(response.get("Failed", []))
            batch = []

    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=args.bucket, Prefix=args.prefix):
        for obj in page.get("Contents", []):
            # S3 events carry URL-encoded keys; the processor decodes them
            body = {"Records": [{"s3": {"bucket": {"name": args.bucket},
                                        "object": {"key": urllib.parse.quote_plus(obj["Key"], safe="/")}}}]}
            batch.append({"Id": str(len(batch)), "MessageBody": json.dumps(body)})
            if len(batch) == 10:
                flush()
    flush()
    print(f"Enqueued {sent} documents for re-ingestion")


if __name__ == "__main__":
    main()

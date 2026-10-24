"""
Chat history and feedback in DynamoDB. One item per question/answer turn, keyed by
user and a time-ordered message id, so a user's history reads newest-first.
"""
import time
import uuid
from decimal import Decimal
from typing import List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .config import settings

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.chat_history_table)
    return _table


def new_message_id() -> str:
    # Millisecond prefix keeps sort order chronological
    return f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}"


def _to_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def from_dynamo(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def save_turn(user_id: str, message_id: str, conversation_id: str, question: str, answer: str,
              sources: List[dict], details: dict) -> None:
    now = int(time.time())
    table().put_item(Item=_to_dynamo({
        "user_id": user_id,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "question": question,
        "answer": answer,
        "sources": sources,
        "created_at": now,
        "expires_at": now + settings.history_ttl_days * 86400,
        **details,
    }))


def list_turns(user_id: str, limit: int = 20, conversation_id: Optional[str] = None) -> List[dict]:
    query = {
        "KeyConditionExpression": Key("user_id").eq(user_id),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if conversation_id:
        query["FilterExpression"] = Attr("conversation_id").eq(conversation_id)
    items = table().query(**query).get("Items", [])
    return [from_dynamo(i) for i in items]


def record_feedback(user_id: str, message_id: str, rating: int, comment: Optional[str]) -> bool:
    try:
        table().update_item(
            Key={"user_id": user_id, "message_id": message_id},
            UpdateExpression="SET feedback = :r, feedback_comment = :c, feedback_at = :t",
            ConditionExpression="attribute_exists(message_id)",
            ExpressionAttributeValues={":r": rating, ":c": comment or "", ":t": int(time.time())},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def record_evaluation(user_id: str, message_id: str, scores: dict) -> None:
    table().update_item(
        Key={"user_id": user_id, "message_id": message_id},
        UpdateExpression="SET evaluation = :e",
        ExpressionAttributeValues={":e": _to_dynamo(scores)},
    )

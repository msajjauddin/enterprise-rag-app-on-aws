"""
Asynchronous, sampled online evaluation. A small Claude model judges a fraction of
answers for faithfulness to the retrieved context and relevance to the question,
after the response has been sent, so it never adds user-facing latency.
"""
import logging
import random
from typing import List

from . import bedrock, history
from .config import settings
from .telemetry import emit_metrics

logger = logging.getLogger(__name__)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {"type": "number", "description": "0-1: every claim in the answer is supported by the context"},
        "answer_relevancy": {"type": "number", "description": "0-1: the answer directly addresses the question"},
        "reason": {"type": "string"},
    },
    "required": ["faithfulness", "answer_relevancy", "reason"],
    "additionalProperties": False,
}

JUDGE_PROMPT = """You are grading an answer produced by a retrieval-augmented assistant.

<question>
{question}
</question>

<context>
{context}
</context>

<answer>
{answer}
</answer>

Score two things from 0 to 1:
- faithfulness: the fraction of the answer's factual claims that the context supports. An answer that correctly says the context lacks the information is fully faithful.
- answer_relevancy: how directly the answer addresses the question.
Give a one-sentence reason."""


def should_sample() -> bool:
    return random.random() < settings.eval_sample_rate


def evaluate_answer(user_id: str, message_id: str, question: str, answer: str, contexts: List[str]) -> None:
    """Background task: judge one answer, emit metrics and store scores on the history item."""
    try:
        scores = bedrock.judge(
            JUDGE_PROMPT.format(question=question, context="\n\n---\n\n".join(contexts), answer=answer),
            JUDGE_SCHEMA,
        )
        faithfulness = min(max(float(scores["faithfulness"]), 0.0), 1.0)
        relevancy = min(max(float(scores["answer_relevancy"]), 0.0), 1.0)
        emit_metrics(
            {"EvalFaithfulness": faithfulness, "EvalAnswerRelevancy": relevancy},
            {"message_id": message_id, "eval_reason": scores.get("reason", "")},
        )
        history.record_evaluation(user_id, message_id, {
            "faithfulness": faithfulness,
            "answer_relevancy": relevancy,
            "reason": scores.get("reason", ""),
            "judge_model": settings.judge_model_id,
        })
    except Exception as e:
        logger.warning(f"Online evaluation failed for {message_id}: {e}")

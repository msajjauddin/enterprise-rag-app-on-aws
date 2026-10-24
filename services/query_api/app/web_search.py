"""
Stateless MCP client for the web-search fallback (Streamable HTTP transport).
Used only when retrieval from the document library is weak. The server URL is
configured per environment, never taken from the request.
"""
import itertools
import json
import logging
import time
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_ids = itertools.count(1)


def _parse_response(response: httpx.Response) -> dict:
    """The server may answer with JSON or a single SSE event carrying JSON."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    return response.json()


def _extract_text(payload: dict) -> Optional[str]:
    if "error" in payload:
        logger.warning(f"MCP error: {payload['error']}")
        return None
    result = payload.get("result", {})
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    return str(result) if result else None


def search(query: str, max_results: int = 5) -> Optional[str]:
    """Call the MCP server's web_search tool; return result text or None."""
    if not settings.mcp_server_url:
        return None
    request = {
        "jsonrpc": "2.0",
        "id": f"req_{next(_ids)}_{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {"name": "web_search", "arguments": {"query": query, "num_results": max_results}},
    }
    try:
        response = httpx.post(
            settings.mcp_server_url,
            json=request,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=settings.mcp_timeout,
        )
        response.raise_for_status()
        return _extract_text(_parse_response(response))
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"MCP web search failed: {e}")
        return None

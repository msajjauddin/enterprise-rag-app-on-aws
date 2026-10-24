"""
Cognito pre-token-generation trigger (V2).
Merges the user's corporate IdP groups (mapped to custom:groups at federation) into
cognito:groups, so both the ID and access tokens carry them. The query service reads
cognito:groups from the access token to filter documents by ACL.
"""
import os
import json
import logging
from typing import List

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

GROUPS_ATTRIBUTE = os.environ.get('GROUPS_ATTRIBUTE', 'custom:groups')
MAX_GROUPS = 100


def parse_groups(raw: str) -> List[str]:
    """
    Parse the IdP groups attribute. SAML multi-valued attributes arrive as
    "[a, b, c]"; OIDC claims may arrive as a JSON array or a comma-separated string.
    """
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith('[') and raw.endswith(']'):
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                return [str(v).strip() for v in values if str(v).strip()]
        except json.JSONDecodeError:
            raw = raw[1:-1]
    return [v.strip().strip('"') for v in raw.split(',') if v.strip().strip('"')]


def handler(event, context):
    request = event.get('request', {})
    attributes = request.get('userAttributes', {})
    existing = request.get('groupConfiguration', {}).get('groupsToOverride', []) or []

    idp_groups = parse_groups(attributes.get(GROUPS_ATTRIBUTE, ''))
    groups = sorted(set(existing) | set(idp_groups))

    if len(groups) > MAX_GROUPS:
        # Keep tokens small; users in that many groups should be handled via app roles
        logger.warning(f"User {attributes.get('sub')} has {len(groups)} groups; truncating to {MAX_GROUPS}")
        groups = groups[:MAX_GROUPS]

    event['response'] = {
        'claimsAndScopeOverrideDetails': {
            'groupOverrideDetails': {
                'groupsToOverride': groups
            }
        }
    }
    return event

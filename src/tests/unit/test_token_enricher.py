"""Test cases for the token_enricher (Cognito pre-token-generation) Lambda function."""
import unittest

from token_enricher.token_enricher import handler, parse_groups


def event(groups_attr=None, cognito_groups=None):
    attributes = {"sub": "user-1", "email": "jane@example.com"}
    if groups_attr is not None:
        attributes["custom:groups"] = groups_attr
    return {
        "version": "2",
        "triggerSource": "TokenGeneration_HostedAuth",
        "request": {
            "userAttributes": attributes,
            "groupConfiguration": {"groupsToOverride": cognito_groups or []},
        },
        "response": {},
    }


class TestTokenEnricher(unittest.TestCase):

    def test_parse_saml_multivalue(self):
        self.assertEqual(parse_groups("[finance, all-staff]"), ["finance", "all-staff"])

    def test_parse_json_array_and_csv(self):
        self.assertEqual(parse_groups('["hr", "all-staff"]'), ["hr", "all-staff"])
        self.assertEqual(parse_groups("hr, it ,"), ["hr", "it"])
        self.assertEqual(parse_groups(""), [])

    def test_merges_idp_and_cognito_groups(self):
        result = handler(event("[finance, all-staff]", ["admins", "finance"]), None)
        groups = result["response"]["claimsAndScopeOverrideDetails"]["groupOverrideDetails"]["groupsToOverride"]
        self.assertEqual(groups, ["admins", "all-staff", "finance"])

    def test_user_without_groups(self):
        result = handler(event(), None)
        groups = result["response"]["claimsAndScopeOverrideDetails"]["groupOverrideDetails"]["groupsToOverride"]
        self.assertEqual(groups, [])

    def test_caps_group_count(self):
        many = "[" + ", ".join(f"g{i:03d}" for i in range(150)) + "]"
        result = handler(event(many), None)
        groups = result["response"]["claimsAndScopeOverrideDetails"]["groupOverrideDetails"]["groupsToOverride"]
        self.assertEqual(len(groups), 100)


if __name__ == "__main__":
    unittest.main()

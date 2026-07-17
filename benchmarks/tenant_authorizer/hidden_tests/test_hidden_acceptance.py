from __future__ import annotations

import json

import pytest

from tenant_auth import Authorizer, Policy


def policy(pid: str, tenant: str, effect: str, action: str, resource: str) -> Policy:
    return Policy(
        id=pid,
        tenant=tenant,
        subject="alice",
        effect=effect,
        action=action,
        resource=resource,
    )


def test_cross_tenant_policy_never_grants() -> None:
    auth = Authorizer([policy("p1", "tenant-a", "allow", "read", "docs/*")])
    decision = auth.decide(tenant="tenant-b", subject="alice", action="read", resource="docs/1")
    assert decision.allowed is False
    assert "tenant" in decision.reason.lower() or "default" in decision.reason.lower()


def test_deny_precedence_and_deterministic_evidence() -> None:
    policies = [
        policy("z-allow", "t", "allow", "read", "docs/*"),
        policy("a-deny", "t", "deny", "read", "docs/private"),
    ]
    auth = Authorizer(policies)
    decision = auth.decide(tenant="t", subject="alice", action="read", resource="docs/private")
    assert decision.allowed is False
    ids = list(decision.policy_ids)
    assert ids == sorted(ids)
    assert {"z-allow", "a-deny"}.issubset(set(ids))


def test_segment_wildcard_does_not_cross_slash() -> None:
    auth = Authorizer([policy("p", "t", "allow", "read/*", "orgs/*/docs/*")])
    assert auth.decide(tenant="t", subject="alice", action="read/item", resource="orgs/acme/docs/1").allowed
    assert not auth.decide(tenant="t", subject="alice", action="read/item", resource="orgs/a/b/docs/1").allowed


def test_json_round_trip_and_unknown_field_rejection() -> None:
    original = Authorizer([policy("p", "t", "allow", "read", "docs/*")])
    encoded = original.to_json()
    restored = Authorizer.from_json(encoded)
    assert restored.decide(tenant="t", subject="alice", action="read", resource="docs/1").allowed
    payload = json.loads(encoded)
    payload["policies"][0]["surprise"] = True
    with pytest.raises((TypeError, ValueError)):
        Authorizer.from_json(json.dumps(payload))


def test_incomplete_request_denies() -> None:
    auth = Authorizer([policy("p", "t", "allow", "read", "docs/*")])
    assert not auth.decide(tenant="", subject="alice", action="read", resource="docs/1").allowed

import pytest
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiteam.audit_trail import AuditTrail
from aiteam.compliance import ComplianceGuard, CompliancePolicy

@pytest.fixture
def temp_audit(tmp_path: Path):
    return AuditTrail(tmp_path)

@pytest.fixture
def compliance_guard(temp_audit):
    policy = CompliancePolicy(environment="prod", require_sensitive_approval=True)
    policy.min_approvers_by_environment["prod"] = 2
    return ComplianceGuard(policy=policy, audit_trail=temp_audit)

def test_audit_trail_records_approval_granted(compliance_guard, temp_audit):
    meta = {
        "task_id": "T001",
        "approved_sensitive_ops": True,
        "approved_by": ["alice", "bob"]
    }
    
    ok, _ = compliance_guard.evaluate_sensitive_approval(meta)
    assert ok is True
    
    docs = temp_audit.records()
    assert len(docs) == 1
    d = docs[0]
    assert d["decision_type"] == "approval_granted"
    assert "ts" in d
    assert d["approver_id"] in ("bob", "alice", "unknown")
    assert d["task_id"] == "T001"

def test_audit_trail_records_approval_denied(compliance_guard, temp_audit):
    # Only 1 approver but 2 required in prod
    meta = {
        "task_id": "T002",
        "approved_sensitive_ops": True,
        "approved_by": ["alice"]
    }
    ok, reason = compliance_guard.evaluate_sensitive_approval(meta)
    assert ok is False
    
    docs = temp_audit.records()
    assert len(docs) == 1
    assert docs[0]["decision_type"] == "approval_denied"
    assert "insufficient_approvers" in docs[0]["reason"]

def test_audit_trail_includes_rule_applied(compliance_guard, temp_audit):
    meta = {
        "task_id": "T003",
        "approved_sensitive_ops": False
    }
    compliance_guard.evaluate_sensitive_approval(meta)
    d = temp_audit.records()[0]
    assert d["rule_applied"] == "missing_approval_flag"

def test_audit_trail_read_window_by_date(temp_audit):
    now = datetime.now(timezone.utc)
    # emit 1 day ago
    temp_audit.audit_decision("test", "T1", "A1", "reason", {})
    # manipulate ts in file
    lines = temp_audit.log_path.read_text().splitlines()
    r = json.loads(lines[0])
    r["ts"] = (now - timedelta(days=1)).isoformat()
    lines[0] = json.dumps(r)
    temp_audit.log_path.write_text("\n".join(lines) + "\n")
    
    # emit today
    temp_audit.audit_decision("test", "T2", "A2", "reason", {})
    
    # window query
    s_date = (now - timedelta(hours=2)).isoformat()
    e_date = (now + timedelta(hours=2)).isoformat()
    docs = temp_audit.windowed_records(s_date, e_date)
    assert len(docs) == 1
    assert docs[0]["task_id"] == "T2"

def test_audit_trail_dedup_on_load(temp_audit):
    temp_audit.audit_decision("test", "T1", "A1", "reason", {})
    records = temp_audit.records()
    assert len(records) == 1
    # duplicate lines identically
    text = temp_audit.log_path.read_text()
    temp_audit.log_path.write_text(text + text)
    # read again
    records_after = temp_audit.records()
    # deduplication by checksum
    assert len(records_after) == 1

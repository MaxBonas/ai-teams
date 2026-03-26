import pytest
from pathlib import Path

from aiteam.config import AlertPolicy
from aiteam.observability import EventLogger

@pytest.fixture
def temp_logger(tmp_path: Path):
    logger = EventLogger(tmp_path)
    
    # 6 tasks: 3 success, 3 fail
    for i in range(3):
        logger.emit("task_execution", {"success": True, "provider": "openai", "channel": "api"})
    for i in range(3):
        logger.emit("task_execution", {"success": False, "provider": "openai", "channel": "api"})
        logger.emit("task_failed", {"reason": "timeout"})
        
    return logger

def test_alert_policy_applied_in_summary(temp_logger):
    # Total=6, Succ=3 => 50% success
    # API share = 100%
    # Failed = 3
    
    # Check default alerts
    summary_default = temp_logger.summary()
    assert summary_default["alert_count"] >= 3 
    
    # Custom policy 
    policy = AlertPolicy(
        min_success_rate_percent=40.0, # 50% > 40%, no alert
        max_api_dependency_percent=100.0, # 100% <= 100%, no alert
        min_execution_count_for_alert=10, # 6 < 10, no alert
        max_recurrent_failures=5 # 3 < 5, no alert
    )
    
    temp_logger.alert_policy = policy
    summary_policy = temp_logger.summary()
    assert summary_policy["alert_count"] == 0

def test_default_alert_policy_fallback(temp_logger):
    # Tests that when policy=None old behavior holds
    assert temp_logger.alert_policy is None
    s = temp_logger.summary()
    assert "low_task_execution_success_rate:50.0" in s["alerts"]
    assert "high_api_dependency:100.0" in s["alerts"]
    assert "recurrent_task_failures:3" in s["alerts"]

def test_custom_alert_thresholds_override(temp_logger):
    policy = AlertPolicy(
        min_success_rate_percent=90.0,
        max_api_dependency_percent=40.0,
        min_execution_count_for_alert=2,
        max_recurrent_failures=1
    )
    temp_logger.alert_policy = policy
    s = temp_logger.summary()
    assert "low_task_execution_success_rate:50.0" in s["alerts"]
    assert "high_api_dependency:100.0" in s["alerts"]
    assert "recurrent_task_failures:3" in s["alerts"]

def test_alert_policy_validation():
    # Dataclass should accept fields
    policy = AlertPolicy(min_success_rate_percent=10.0)
    assert policy.min_success_rate_percent == 10.0

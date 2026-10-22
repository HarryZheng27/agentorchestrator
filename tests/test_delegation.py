"""Tests for delegation parsing, loop prevention, and permissions."""

import pytest
from agent_orchestrator.orchestration.delegation import DelegationManager, DelegationRequest


def test_parse_delegations_xml():
    dm = DelegationManager()
    text = (
        "I will divide this task into two parts.\n"
        "<delegate target=\"coder\">Write the database schema</delegate>\n"
        "And also:\n"
        "<delegate target=\"reviewer\" context=\"Ensure sqlite compatibility\">Review schema</delegate>"
    )
    reqs = dm.parse_delegations(text)
    assert len(reqs) == 2
    assert reqs[0].target_agent == "coder"
    assert reqs[0].task == "Write the database schema"
    assert reqs[1].target_agent == "reviewer"
    assert reqs[1].context == "Ensure sqlite compatibility"
    assert reqs[1].task == "Review schema"


def test_parse_delegations_self_closing():
    dm = DelegationManager()
    text = '<delegate agent="tester" prompt="Run pytest suite" />'
    reqs = dm.parse_delegations(text)
    assert len(reqs) == 1
    assert reqs[0].target_agent == "tester"
    assert reqs[0].task == "Run pytest suite"


def test_parse_delegations_tool_call():
    dm = DelegationManager()
    text = 'TOOL_CALL: delegate_task({"target_agent": "dev", "task": "refactor api.py"})'
    reqs = dm.parse_delegations(text)
    assert len(reqs) == 1
    assert reqs[0].target_agent == "dev"
    assert reqs[0].task == "refactor api.py"


def test_parse_delegations_json_block():
    dm = DelegationManager()
    text = '```json\n{"action": "delegate", "target": "worker", "task": "format output"}\n```'
    reqs = dm.parse_delegations(text)
    assert len(reqs) == 1
    assert reqs[0].target_agent == "worker"
    assert reqs[0].task == "format output"

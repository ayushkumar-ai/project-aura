from core.models import AURARequest
from core.policy import Policy, PolicyDecision


def test_policy_allows_valid_request():
    policy = Policy()
    request = AURARequest(user_input="Hello AURA")

    assert policy.evaluate(request) == PolicyDecision.ALLOW


def test_policy_denies_empty_request():
    policy = Policy()
    request = AURARequest(user_input="   ")

    assert policy.evaluate(request) == PolicyDecision.DENY


def test_policy_authorizes_default_tools():
    policy = Policy()

    assert policy.authorize_tool("calculator") == PolicyDecision.ALLOW
    assert policy.authorize_tool("echo") == PolicyDecision.ALLOW
    assert policy.authorize_tool("unknown") == PolicyDecision.DENY


def test_policy_authorizes_custom_tool_whitelist():
    policy = Policy(authorized_tools={"custom_tool"})

    assert policy.authorize_tool("custom_tool") == PolicyDecision.ALLOW
    assert policy.authorize_tool("calculator") == PolicyDecision.DENY
    assert policy.authorize_tool("echo") == PolicyDecision.DENY

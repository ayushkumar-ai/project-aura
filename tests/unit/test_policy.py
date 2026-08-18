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

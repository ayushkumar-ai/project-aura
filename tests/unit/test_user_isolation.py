import pytest
from core.history import ConversationHistory
from core.durable_state_store import DurablePersonalStateStore
from core.personal_state_types import MemoryCategory, UserPreferences
from memory.in_memory import InMemoryStore
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import RetrievalQuery, RetrievalSourceType
from core.policy import Policy, PolicyDecision
from core.identity import UserIdentity, UserRole, UserScope


def test_conversation_history_isolation():
    history = ConversationHistory()
    history.add_turn(user_input="Hello from Alice", assistant_output="Hi Alice", user_id="alice")
    history.add_turn(user_input="Hello from Bob", assistant_output="Hi Bob", user_id="bob")
    history.add_turn(user_input="Alice question", assistant_output="Alice answer", user_id="alice")

    alice_turns = history.get_turns("alice")
    bob_turns = history.get_turns("bob")

    assert len(alice_turns) == 2
    assert all(t.user_id == "alice" for t in alice_turns)

    assert len(bob_turns) == 1
    assert bob_turns[0].user_input == "Hello from Bob"

    # Clear only Bob
    history.clear(user_id="bob")
    assert len(history.get_turns("bob")) == 0
    assert len(history.get_turns("alice")) == 2


def test_durable_state_store_preferences_isolation():
    store = DurablePersonalStateStore()
    store.update_preferences({"preferred_name": "Alice Cooper", "verbosity": 1}, user_id="user_alice")
    store.update_preferences({"preferred_name": "Bob Marley", "verbosity": 5}, user_id="user_bob")

    p_alice = store.get_preferences("user_alice")
    p_bob = store.get_preferences("user_bob")

    assert p_alice.preferred_name == "Alice Cooper"
    assert p_alice.verbosity == 1

    assert p_bob.preferred_name == "Bob Marley"
    assert p_bob.verbosity == 5


def test_durable_state_store_memory_isolation():
    store = DurablePersonalStateStore()
    store.record_memory(
        category=MemoryCategory.SEMANTIC,
        content="Alice secret confidential project plan",
        user_id="alice",
    )
    store.record_memory(
        category=MemoryCategory.SEMANTIC,
        content="Bob public recipe for apple pie",
        user_id="bob",
    )

    alice_mems = store.query_memories(user_id="alice")
    bob_mems = store.query_memories(user_id="bob")

    assert len(alice_mems) == 1
    assert "Alice secret" in alice_mems[0].content

    assert len(bob_mems) == 1
    assert "apple pie" in bob_mems[0].content


def test_durable_state_store_experience_isolation():
    store = DurablePersonalStateStore()
    store.record_experience(
        task_description="Alice deployment workflow",
        plan_summary="Deploy server",
        user_id="alice",
    )
    store.record_experience(
        task_description="Bob testing workflow",
        plan_summary="Run tests",
        user_id="bob",
    )

    alice_exps = store.query_experiences(user_id="alice")
    bob_exps = store.query_experiences(user_id="bob")

    assert len(alice_exps) == 1
    assert "Alice deployment" in alice_exps[0].task_description

    assert len(bob_exps) == 1
    assert "Bob testing" in bob_exps[0].task_description


def test_rag_pipeline_user_memory_isolation():
    store = DurablePersonalStateStore()
    store.record_memory(category=MemoryCategory.SEMANTIC, content="Alice credit card 1234", user_id="alice")
    store.record_memory(category=MemoryCategory.SEMANTIC, content="Bob dog name Charlie", user_id="bob")

    pipeline = AdvancedRetrievalPipeline(durable_state_store=store)
    pipeline.add_knowledge_document("doc1", "Global FAQ", "AURA is an autonomous AI assistant.")

    # Query as Bob
    bob_bundle = pipeline.execute_rag(query="credit card or dog", user_id="bob")
    assert "Charlie" in bob_bundle.assembled_text
    assert "1234" not in bob_bundle.assembled_text

    # Query as Alice
    alice_bundle = pipeline.execute_rag(query="credit card or dog", user_id="alice")
    assert "1234" in alice_bundle.assembled_text
    assert "Charlie" not in alice_bundle.assembled_text


def test_policy_tool_authorization_with_identity():
    policy = Policy()
    admin_ident = UserIdentity(
        user_id="adm",
        username="Admin",
        roles=frozenset({UserRole.ADMIN}),
    )
    user_ident = UserIdentity(
        user_id="usr",
        username="User",
        roles=frozenset({UserRole.USER}),
        scopes=frozenset({UserScope.RUN.value}),
    )

    # Standard tool
    assert policy.authorize_tool("calculator", identity=user_ident) == PolicyDecision.ALLOW
    assert policy.authorize_tool("calculator", identity=admin_ident) == PolicyDecision.ALLOW

    # Privileged tool (system_info / execution_tool)
    assert policy.authorize_tool("system_info", identity=admin_ident) == PolicyDecision.ALLOW
    assert policy.authorize_tool("system_info", identity=user_ident) == PolicyDecision.DENY

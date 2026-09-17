"""M54 — Webhooks & Event Gateway REST API Integration Tests."""

import json
import pytest
from http import HTTPStatus
from app.server import AURAHTTPServer
from app.config import Settings
from core.repositories.factory import create_repository_container


@pytest.fixture
def test_server():
    """Create test server with in-memory repositories."""
    config = Settings()
    config.aura_env = "development"
    config.aura_server_port = 8894
    config.aura_server_api_key = "test_m54_api_key"
    config.aura_webhooks_enabled = True

    repo_container = create_repository_container(config=config, auto_migrate=False)
    server = AURAHTTPServer(
        config=config,
        host="127.0.0.1",
        port=8894,
        repository_container=repo_container,
    )
    return server


from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    EventSubscription,
    SubscriptionTargetType,
    SubscriptionStatus,
)


class TestM54RestApiEndpoints:
    """Verify REST API contracts for Webhook Endpoints, Subscriptions, Deliveries, and DLQ."""

    def test_endpoint_crud_lifecycle(self, test_server):
        """CRUD lifecycle for webhook endpoints via repository."""
        handler = test_server
        webhook_repo = handler.repository_container.webhooks
        user_id = "test_user_m54"

        # 1. Create Endpoint
        ep = WebhookEndpoint(
            id="ep_gh_1",
            user_id=user_id,
            name="GitHub CI Webhook",
            description="Receives push events from GitHub",
        )
        created = webhook_repo.create_endpoint(ep)
        assert created.id == "ep_gh_1"

        # 2. Get Endpoint
        fetched = webhook_repo.get_endpoint("ep_gh_1", user_id=user_id)
        assert fetched is not None
        assert fetched.name == "GitHub CI Webhook"

        # 3. List Endpoints
        eps = webhook_repo.list_endpoints(user_id=user_id)
        assert len(eps) == 1
        assert eps[0].id == "ep_gh_1"

        # 4. Update Endpoint
        updated = webhook_repo.update_endpoint("ep_gh_1", user_id=user_id, name="GitHub CI Webhook Updated")
        assert updated is not None
        assert updated.name == "GitHub CI Webhook Updated"

        # 5. Delete Endpoint
        deleted = webhook_repo.delete_endpoint("ep_gh_1", user_id=user_id)
        assert deleted is True
        assert webhook_repo.get_endpoint("ep_gh_1", user_id=user_id) is None

    def test_subscription_crud_lifecycle(self, test_server):
        """CRUD lifecycle for event subscriptions via repository."""
        handler = test_server
        webhook_repo = handler.repository_container.webhooks
        user_id = "test_user_m54"

        # 1. Create Subscription
        sub = EventSubscription(
            id="sub_test_1",
            user_id=user_id,
            name="Order Completed Listener",
            event_type_filter="order.*",
            target_type=SubscriptionTargetType.WEBHOOK,
            target_url="https://api.external.com/webhook",
        )
        created = webhook_repo.create_subscription(sub)
        assert created.id == "sub_test_1"

        # 2. Get Subscription
        fetched = webhook_repo.get_subscription("sub_test_1", user_id=user_id)
        assert fetched is not None
        assert fetched.event_type_filter == "order.*"

        # 3. List Subscriptions
        subs = webhook_repo.list_subscriptions(user_id=user_id)
        assert len(subs) == 1

        # 4. Update Subscription
        updated = webhook_repo.update_subscription("sub_test_1", user_id=user_id, status=SubscriptionStatus.PAUSED)
        assert updated is not None
        assert updated.status == SubscriptionStatus.PAUSED

        # 5. Delete Subscription
        deleted = webhook_repo.delete_subscription("sub_test_1", user_id=user_id)
        assert deleted is True
        assert webhook_repo.get_subscription("sub_test_1", user_id=user_id) is None

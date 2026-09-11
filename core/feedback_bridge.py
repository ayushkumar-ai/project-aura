"""Evaluation Feedback Bridge Engine (M24).

Translates M23 EvaluationReport and TrajectoryEvaluation into validated feedback
events and routes them to the AdaptivePolicyOptimizer.
"""

from __future__ import annotations

import logging
from typing import Any

from core.adaptive_optimizer import AdaptivePolicyOptimizer, OptimizationEvent
from evaluation.models import EvaluationReport

logger = logging.getLogger("aura.feedback_bridge")


class FeedbackBridge:
    """Bridges evaluation results to the closed-loop adaptive policy optimizer."""

    def __init__(
        self,
        optimizer: AdaptivePolicyOptimizer | None = None,
        event_dispatcher: Any | None = None,
        auto_subscribe: bool = False,
    ):
        self.optimizer = optimizer if optimizer is not None else AdaptivePolicyOptimizer()
        self.event_dispatcher = event_dispatcher
        self.subscription_id: str | None = None

        if auto_subscribe and self.event_dispatcher is not None and hasattr(self.event_dispatcher, "subscribe"):
            self.subscribe_to_dispatcher()

    def subscribe_to_dispatcher(self) -> None:
        """Register event subscription on the event dispatcher."""
        if self.event_dispatcher is not None and hasattr(self.event_dispatcher, "subscribe"):
            try:
                sub = self.event_dispatcher.subscribe(
                    topic_pattern="aura.evaluation.completed",
                    goal_id="feedback_bridge_system",
                )
                self.subscription_id = sub.subscription_id
            except Exception as e:
                logger.warning("Could not subscribe FeedbackBridge: %s", e)

    def process_evaluation(
        self,
        report: EvaluationReport,
        goal: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[OptimizationEvent]:
        """Process evaluation report and apply adaptive policy optimizations."""
        if not isinstance(report, EvaluationReport):
            raise TypeError("report must be an EvaluationReport instance.")

        return self.optimizer.optimize_from_evaluation(
            report=report,
            goal=goal,
            context=context,
        )

    def _on_evaluation_completed_event(self, event: Any) -> None:
        """Async event handler for aura.evaluation.completed events."""
        try:
            payload = getattr(event, "payload", None)
            if isinstance(payload, EvaluationReport):
                self.process_evaluation(payload)
        except Exception as e:
            logger.error("FeedbackBridge error processing event: %s", e)

import logging
import time
from typing import Any
from uuid import uuid4

from core.agent_delegation import (
    DelegationContract,
    DelegationResult,
    DelegationStatus,
    DelegationTree,
)
from core.agent_message_bus import AgentMessageBus
from core.agent_message_types import AgentMessageType
from core.agent_role import AgentRole
from core.consensus_engine import AgentVote, ConsensusEngine, ConsensusStrategy
from core.model_router import ModelRouter, TaskRequirements
from core.policy import Policy
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.role_registry import RoleRegistry
from core.session_types import StreamEventType
from core.skill_registry import SkillRegistry
from core.streaming_gateway import StreamingGateway
from core.task_planner import TaskPlanner
from core.team_types import (
    TeamDefinition,
    TeamExecutionResult,
    TeamMember,
    TeamTopology,
)
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.team_orchestrator")


class TeamOrchestrator:
    """Coordinates specialized agent personas across multi-agent collaborative team topologies."""

    def __init__(
        self,
        role_registry: RoleRegistry | None = None,
        message_bus: AgentMessageBus | None = None,
        delegation_tree: DelegationTree | None = None,
        consensus_engine: ConsensusEngine | None = None,
        model_router: ModelRouter | None = None,
        model: ModelInterface | None = None,
        planner: TaskPlanner | None = None,
        skill_registry: SkillRegistry | None = None,
        streaming_gateway: StreamingGateway | None = None,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        policy: Policy | None = None,
        tool_executor: Any | None = None,
        approval_gateway: Any | None = None,
        max_team_members: int = 20,
        max_delegation_depth: int = 3,
        default_timeout: float = 300.0,
    ):
        self.role_registry = role_registry if role_registry is not None else RoleRegistry()
        self.message_bus = message_bus if message_bus is not None else AgentMessageBus()
        self.delegation_tree = delegation_tree if delegation_tree is not None else DelegationTree()
        self.consensus_engine = consensus_engine if consensus_engine is not None else ConsensusEngine()
        self.model_router = model_router
        self.model = model
        self.planner = planner
        self.skill_registry = skill_registry if skill_registry is not None else SkillRegistry()
        self.streaming_gateway = streaming_gateway
        self.budget_manager = budget_manager
        self.lock_manager = lock_manager
        self.policy = policy
        self.tool_executor = tool_executor
        self.approval_gateway = approval_gateway
        self.max_team_members = max_team_members
        self.max_delegation_depth = max_delegation_depth
        self.default_timeout = default_timeout

    def execute_team(
        self,
        task: str,
        team: TeamDefinition | None = None,
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> TeamExecutionResult:
        """Execute a collaborative multi-agent task across the specified team topology."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("Task description must be a non-empty string.")

        start_time = time.time()
        eff_team = team if team is not None else self._build_default_team()

        if len(eff_team.members) > self.max_team_members:
            raise ValueError(
                f"Team size ({len(eff_team.members)}) exceeds maximum team members limit ({self.max_team_members})."
            )

        # Check resource budget if manager is configured
        if self.budget_manager is not None:
            alloc = self.budget_manager.acquire_quota(
                goal_id=eff_team.team_id,
                estimated_tokens=250,
                estimated_tool_calls=1,
            )
            if not alloc.is_granted:
                return TeamExecutionResult(
                    team_id=eff_team.team_id,
                    task=task,
                    topology=eff_team.topology,
                    success=False,
                    final_output=f"Team execution halted: {alloc.reason}",
                    error=alloc.reason,
                )

        # Emit streaming event
        if self.streaming_gateway is not None:
            self.streaming_gateway.create_and_publish(
                session_id=session_id,
                event_type=StreamEventType.STEP_STARTED,
                data={
                    "team_id": eff_team.team_id,
                    "topology": eff_team.topology.value,
                    "task": task,
                    "members": [m.role_id for m in eff_team.members],
                },
            )

        try:
            if eff_team.topology == TeamTopology.HIERARCHICAL:
                res = self._execute_hierarchical(task, eff_team, session_id)
            elif eff_team.topology == TeamTopology.SEQUENTIAL_PIPELINE:
                res = self._execute_sequential_pipeline(task, eff_team, session_id)
            elif eff_team.topology == TeamTopology.ROUND_ROBIN_DEBATE:
                res = self._execute_round_robin_debate(task, eff_team, session_id)
            elif eff_team.topology == TeamTopology.CONSENSUS_VOTING:
                res = self._execute_consensus_voting(task, eff_team, session_id)
            else:
                raise ValueError(f"Unsupported team topology: {eff_team.topology}")

            elapsed = time.time() - start_time
            res.total_latency_seconds = elapsed

            # Record consumption in budget manager
            if self.budget_manager is not None:
                self.budget_manager.release_quota(goal_id=eff_team.team_id, actual_tokens=250, actual_tool_calls=1)

            # Emit streaming completion
            if self.streaming_gateway is not None:
                self.streaming_gateway.create_and_publish(
                    session_id=session_id,
                    event_type=StreamEventType.STEP_COMPLETED,
                    data={"result": res.final_output, "success": res.success},
                )

            return res

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("Team '%s' execution failed: %s", eff_team.team_id, e)
            if self.streaming_gateway is not None:
                self.streaming_gateway.create_and_publish(
                    session_id=session_id,
                    event_type=StreamEventType.ERROR,
                    data={"error": str(e)},
                )
            return TeamExecutionResult(
                team_id=eff_team.team_id,
                task=task,
                topology=eff_team.topology,
                success=False,
                final_output=f"Team execution error: {e}",
                total_latency_seconds=elapsed,
                error=str(e),
            )

    def delegate(
        self,
        contract: DelegationContract,
        session_id: str = "default",
        team_id: str = "default_team",
    ) -> DelegationResult:
        """Execute a formal task delegation from one role to another."""
        start_t = time.time()
        self.delegation_tree.register_delegation(contract, role_registry=self.role_registry)

        # Publish delegation message onto bus
        self.message_bus.send_direct(
            sender=contract.delegator_role_id,
            recipient=contract.delegatee_role_id,
            content=contract.task_description,
            message_type=AgentMessageType.TASK_DELEGATION,
            payload=contract.context,
            session_id=session_id,
            team_id=team_id,
        )

        try:
            output = self._execute_role_task(
                role_id=contract.delegatee_role_id,
                task=contract.task_description,
                context=contract.context,
                session_id=session_id,
            )
            elapsed = time.time() - start_t
            result = DelegationResult(
                delegation_id=contract.delegation_id,
                delegator_role_id=contract.delegator_role_id,
                delegatee_role_id=contract.delegatee_role_id,
                status=DelegationStatus.COMPLETED,
                output=output,
                latency_seconds=elapsed,
                is_untrusted=contract.is_untrusted,
            )

            # Send result message back
            self.message_bus.send_direct(
                sender=contract.delegatee_role_id,
                recipient=contract.delegator_role_id,
                content=output,
                message_type=AgentMessageType.TASK_RESULT,
                session_id=session_id,
                team_id=team_id,
            )

        except Exception as e:
            elapsed = time.time() - start_t
            result = DelegationResult(
                delegation_id=contract.delegation_id,
                delegator_role_id=contract.delegator_role_id,
                delegatee_role_id=contract.delegatee_role_id,
                status=DelegationStatus.FAILED,
                output="",
                latency_seconds=elapsed,
                error=str(e),
            )

        self.delegation_tree.complete_delegation(result)
        return result

    def _execute_hierarchical(
        self,
        task: str,
        team: TeamDefinition,
        session_id: str,
    ) -> TeamExecutionResult:
        """Lead agent delegates subtasks to specialist member roles and synthesizes output."""
        lead_member = team.get_lead_member()
        lead_role_id = lead_member.role_id if lead_member else "coordinator"

        subtask_results: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []

        # 1. Lead defines initial strategy
        lead_plan = self._execute_role_task(
            role_id=lead_role_id,
            task=f"Analyze and create subtask directives for: {task}",
            session_id=session_id,
        )
        subtask_results[f"{lead_role_id}_plan"] = lead_plan
        artifacts.append({"role_id": lead_role_id, "summary": "Initial Coordination Plan", "content": lead_plan})

        # 2. Delegate to each non-lead member
        for m in team.members:
            if m.role_id == lead_role_id:
                continue

            contract = DelegationContract(
                delegator_role_id=lead_role_id,
                delegatee_role_id=m.role_id,
                task_description=f"Perform role objective for task '{task}' based on plan:\n{lead_plan}",
                max_depth=self.max_delegation_depth,
            )
            del_res = self.delegate(contract, session_id=session_id, team_id=team.team_id)
            subtask_results[m.role_id] = del_res.output if del_res.status == DelegationStatus.COMPLETED else del_res.error
            artifacts.append({
                "role_id": m.role_id,
                "summary": f"Specialist Deliverable",
                "content": del_res.output or str(del_res.error),
            })

        # 3. Lead synthesizes final deliverable
        final_synth = self.consensus_engine.synthesize_artifacts(artifacts, lead_role_id=lead_role_id)
        msg_count = len(self.message_bus.get_history(team_id=team.team_id, session_id=session_id))

        return TeamExecutionResult(
            team_id=team.team_id,
            task=task,
            topology=team.topology,
            success=True,
            final_output=final_synth,
            subtask_results=subtask_results,
            messages_exchanged=msg_count,
            iterations=1,
            consensus_score=1.0,
        )

    def _execute_sequential_pipeline(
        self,
        task: str,
        team: TeamDefinition,
        session_id: str,
    ) -> TeamExecutionResult:
        """Sequential handoff chain through member roles."""
        current_input = task
        subtask_results: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []

        for i, m in enumerate(team.members, 1):
            role_task = (
                f"Initial task: {task}\n\nCurrent stage pipeline input from previous stages:\n{current_input}"
                if i > 1
                else task
            )
            output = self._execute_role_task(
                role_id=m.role_id,
                task=role_task,
                session_id=session_id,
            )
            subtask_results[m.role_id] = output
            artifacts.append({
                "role_id": m.role_id,
                "summary": f"Pipeline Stage {i}",
                "content": output,
            })
            current_input = output

            # Send handoff message
            next_role = team.members[i].role_id if i < len(team.members) else "coordinator"
            self.message_bus.send_direct(
                sender=m.role_id,
                recipient=next_role,
                content=output,
                message_type=AgentMessageType.HANDOFF,
                session_id=session_id,
                team_id=team.team_id,
            )

        final_synth = self.consensus_engine.synthesize_artifacts(artifacts)
        msg_count = len(self.message_bus.get_history(team_id=team.team_id, session_id=session_id))

        return TeamExecutionResult(
            team_id=team.team_id,
            task=task,
            topology=team.topology,
            success=True,
            final_output=current_input if len(artifacts) == 1 else final_synth,
            subtask_results=subtask_results,
            messages_exchanged=msg_count,
            iterations=len(team.members),
            consensus_score=1.0,
        )

    def _execute_round_robin_debate(
        self,
        task: str,
        team: TeamDefinition,
        session_id: str,
    ) -> TeamExecutionResult:
        """Peer debate with iterative refinement."""
        artifacts: list[dict[str, Any]] = []
        subtask_results: dict[str, Any] = {}
        current_proposal = task
        iteration = 0

        while iteration < team.max_iterations:
            iteration += 1
            for m in team.members:
                critique_prompt = (
                    f"Task: {task}\n\nCurrent Working Proposal:\n{current_proposal}\n\n"
                    f"Critique this proposal from the perspective of your role [{m.role_id}], "
                    f"identify flaws, and provide a revised, improved proposal."
                )
                output = self._execute_role_task(
                    role_id=m.role_id,
                    task=critique_prompt,
                    session_id=session_id,
                )
                current_proposal = output
                subtask_results[f"{m.role_id}_iter_{iteration}"] = output
                artifacts.append({
                    "role_id": m.role_id,
                    "summary": f"Debate Iteration {iteration}",
                    "content": output,
                })
                self.message_bus.broadcast(
                    sender=m.role_id,
                    content=output,
                    message_type=AgentMessageType.CRITIQUE,
                    session_id=session_id,
                    team_id=team.team_id,
                )

        final_synth = self.consensus_engine.synthesize_artifacts(artifacts)
        msg_count = len(self.message_bus.get_history(team_id=team.team_id, session_id=session_id))

        return TeamExecutionResult(
            team_id=team.team_id,
            task=task,
            topology=team.topology,
            success=True,
            final_output=current_proposal,
            subtask_results=subtask_results,
            messages_exchanged=msg_count,
            iterations=iteration,
            consensus_score=0.9,
        )

    def _execute_consensus_voting(
        self,
        task: str,
        team: TeamDefinition,
        session_id: str,
    ) -> TeamExecutionResult:
        """Parallel peer voting and aggregation."""
        votes: list[AgentVote] = []
        subtask_results: dict[str, Any] = {}

        for m in team.members:
            vote_prompt = (
                f"Evaluate the following task/question and provide your clear decision, confidence score, and rationale:\n{task}"
            )
            output = self._execute_role_task(
                role_id=m.role_id,
                task=vote_prompt,
                session_id=session_id,
            )
            subtask_results[m.role_id] = output

            # Extract decision or use output as decision
            decision = output.strip().split("\n")[0][:100]
            vote = AgentVote(
                voter_role_id=m.role_id,
                decision=decision,
                confidence_score=0.9,
                weight=m.weight,
                rationale=output,
            )
            votes.append(vote)
            self.message_bus.send_direct(
                sender=m.role_id,
                recipient="coordinator",
                content=output,
                message_type=AgentMessageType.VOTE,
                payload={"decision": decision, "confidence": 0.9},
                session_id=session_id,
                team_id=team.team_id,
            )

        consensus = self.consensus_engine.evaluate_consensus(
            votes=votes,
            strategy=team.consensus_strategy,
            lead_role_id=team.get_lead_member().role_id if team.get_lead_member() else None,
        )

        msg_count = len(self.message_bus.get_history(team_id=team.team_id, session_id=session_id))

        return TeamExecutionResult(
            team_id=team.team_id,
            task=task,
            topology=team.topology,
            success=consensus.reached,
            final_output=f"Consensus Result: {consensus.synthesis_summary}\nSelected: {consensus.selected_decision}",
            subtask_results=subtask_results,
            messages_exchanged=msg_count,
            iterations=1,
            consensus_score=consensus.confidence,
            metadata={"consensus": consensus.to_dict()},
        )

    def _execute_role_task(
        self,
        role_id: str,
        task: str,
        context: dict[str, Any] | None = None,
        session_id: str = "default",
    ) -> str:
        """Execute a prompt/task using the specialized persona and model requirements of a role."""
        role = self.role_registry.get_role(role_id)

        prompt = (
            f"=== ROLE: {role.name} ({role.role_id}) ===\n"
            f"{role.system_prompt}\n\n"
            f"=== TASK ===\n{task}\n"
        )
        if context:
            prompt += f"\n=== CONTEXT ===\n{context}\n"

        req_id = uuid4()
        reqs = TaskRequirements(required_capabilities=set(role.required_capabilities))

        # Model routing resolution
        if self.model_router is not None:
            if hasattr(self.model_router, "generate_with_fallback"):
                resp, _ = self.model_router.generate_with_fallback(prompt=prompt, request_id=req_id, requirements=reqs)
            else:
                route_res = self.model_router.route(reqs)
                resp = route_res.provider.generate(prompt=prompt, request_id=req_id)
        elif self.model is not None:
            resp = self.model.generate(prompt=prompt, request_id=req_id)
        else:
            # Fallback simulated response
            return f"[{role.name}] Executed role task for: {task[:60]}..."

        return resp.content if resp and hasattr(resp, "content") else str(resp)

    def _build_default_team(self) -> TeamDefinition:
        """Construct standard default team with Coordinator, Architect, Coder, and Reviewer."""
        return TeamDefinition(
            team_id="default_dev_team",
            name="Default Development Team",
            description="Autonomous software development team with lead, architect, coder, and reviewer.",
            members=[
                TeamMember(role_id="coordinator", is_lead=True),
                TeamMember(role_id="architect"),
                TeamMember(role_id="coder"),
                TeamMember(role_id="reviewer"),
            ],
            topology=TeamTopology.HIERARCHICAL,
        )

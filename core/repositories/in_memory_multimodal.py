"""M57 — In-Memory Multimodal Repository Implementation.

Provides a thread-safe, high-fidelity in-memory store for multimodal artifacts,
jobs, structured results, derivations, and capability usage with 100% semantic parity.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.multimodal.types import (
    ArtifactLifecycleState,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalDerivation,
    MultimodalProcessingJob,
    MultimodalResult,
)
from core.repositories.base_multimodal import BaseMultimodalRepository

logger = logging.getLogger("aura.repositories.in_memory_multimodal")


class InMemoryMultimodalRepository(BaseMultimodalRepository):
    """Thread-safe in-memory multimodal repository for tests and local development."""

    def __init__(self):
        self._lock = threading.RLock()
        self._artifacts: dict[str, dict[str, MultimodalArtifact]] = {}  # tenant_id -> artifact_id -> artifact
        self._jobs: dict[str, dict[str, MultimodalProcessingJob]] = {}  # tenant_id -> job_id -> job
        self._results: dict[str, dict[str, MultimodalResult]] = {}      # tenant_id -> result_id -> result
        self._derivations: dict[str, dict[str, MultimodalDerivation]] = {}  # tenant_id -> derivation_id -> derivation
        self._usage: dict[str, list[MultimodalCapabilityUsage]] = {}    # tenant_id -> list[usage]

    def save_artifact(self, artifact: MultimodalArtifact) -> MultimodalArtifact:
        with self._lock:
            t_id = artifact.tenant_id
            if t_id not in self._artifacts:
                self._artifacts[t_id] = {}
            artifact.updated_at = time.time()
            self._artifacts[t_id][artifact.artifact_id] = artifact
            return artifact

    def get_artifact(self, artifact_id: str, tenant_id: str) -> MultimodalArtifact | None:
        with self._lock:
            return self._artifacts.get(tenant_id, {}).get(artifact_id)

    def list_artifacts(
        self,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MultimodalArtifact]:
        with self._lock:
            all_arts = list(self._artifacts.get(tenant_id, {}).values())
            if lifecycle_state is not None:
                state_val = lifecycle_state.value if isinstance(lifecycle_state, ArtifactLifecycleState) else str(lifecycle_state)
                all_arts = [
                    a for a in all_arts
                    if (a.lifecycle_state.value if isinstance(a.lifecycle_state, ArtifactLifecycleState) else str(a.lifecycle_state)) == state_val
                ]
            all_arts.sort(key=lambda a: a.created_at, reverse=True)
            return all_arts[offset : offset + limit]

    def update_artifact_state(
        self,
        artifact_id: str,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str,
        reason: str = "",
    ) -> MultimodalArtifact | None:
        with self._lock:
            art = self.get_artifact(artifact_id, tenant_id=tenant_id)
            if not art:
                return None
            target_state = lifecycle_state if isinstance(lifecycle_state, ArtifactLifecycleState) else ArtifactLifecycleState(lifecycle_state)
            art.lifecycle_state = target_state
            art.updated_at = time.time()
            if reason:
                art.metadata["state_change_reason"] = reason
            return art

    def delete_artifact(self, artifact_id: str, tenant_id: str) -> bool:
        with self._lock:
            if tenant_id in self._artifacts and artifact_id in self._artifacts[tenant_id]:
                del self._artifacts[tenant_id][artifact_id]
                # Cascade deletion to jobs
                if tenant_id in self._jobs:
                    job_ids_to_del = [jid for jid, j in self._jobs[tenant_id].items() if j.artifact_id == artifact_id]
                    for jid in job_ids_to_del:
                        del self._jobs[tenant_id][jid]
                # Cascade deletion to results
                if tenant_id in self._results:
                    res_ids_to_del = [rid for rid, r in self._results[tenant_id].items() if r.artifact_id == artifact_id]
                    for rid in res_ids_to_del:
                        del self._results[tenant_id][rid]
                # Cascade deletion to derivations
                if tenant_id in self._derivations:
                    drv_ids_to_del = [did for did, d in self._derivations[tenant_id].items() if d.source_artifact_id == artifact_id]
                    for did in drv_ids_to_del:
                        del self._derivations[tenant_id][did]
                return True
            return False

    def save_job(self, job: MultimodalProcessingJob) -> MultimodalProcessingJob:
        with self._lock:
            t_id = job.tenant_id
            if t_id not in self._jobs:
                self._jobs[t_id] = {}
            self._jobs[t_id][job.job_id] = job
            return job

    def get_job(self, job_id: str, tenant_id: str) -> MultimodalProcessingJob | None:
        with self._lock:
            return self._jobs.get(tenant_id, {}).get(job_id)

    def get_job_by_idempotency(self, tenant_id: str, idempotency_key: str) -> MultimodalProcessingJob | None:
        with self._lock:
            for job in self._jobs.get(tenant_id, {}).values():
                if job.idempotency_key == idempotency_key:
                    return job
            return None

    def save_result(self, result: MultimodalResult) -> MultimodalResult:
        with self._lock:
            t_id = result.tenant_id
            if t_id not in self._results:
                self._results[t_id] = {}
            self._results[t_id][result.result_id] = result
            return result

    def get_result(self, result_id: str, tenant_id: str) -> MultimodalResult | None:
        with self._lock:
            return self._results.get(tenant_id, {}).get(result_id)

    def list_results(self, artifact_id: str, tenant_id: str) -> list[MultimodalResult]:
        with self._lock:
            res = [r for r in self._results.get(tenant_id, {}).values() if r.artifact_id == artifact_id]
            res.sort(key=lambda r: r.created_at)
            return res

    def save_derivation(self, derivation: MultimodalDerivation) -> MultimodalDerivation:
        with self._lock:
            t_id = derivation.tenant_id
            if t_id not in self._derivations:
                self._derivations[t_id] = {}
            self._derivations[t_id][derivation.derivation_id] = derivation
            return derivation

    def list_derivations(self, source_artifact_id: str, tenant_id: str) -> list[MultimodalDerivation]:
        with self._lock:
            return [d for d in self._derivations.get(tenant_id, {}).values() if d.source_artifact_id == source_artifact_id]

    def save_usage(self, usage: MultimodalCapabilityUsage) -> MultimodalCapabilityUsage:
        with self._lock:
            t_id = usage.tenant_id
            if t_id not in self._usage:
                self._usage[t_id] = []
            self._usage[t_id].append(usage)
            return usage

    def purge_tenant_data(self, tenant_id: str) -> int:
        with self._lock:
            count = 0
            if tenant_id in self._artifacts:
                count += len(self._artifacts[tenant_id])
                del self._artifacts[tenant_id]
            if tenant_id in self._jobs:
                count += len(self._jobs[tenant_id])
                del self._jobs[tenant_id]
            if tenant_id in self._results:
                count += len(self._results[tenant_id])
                del self._results[tenant_id]
            if tenant_id in self._derivations:
                count += len(self._derivations[tenant_id])
                del self._derivations[tenant_id]
            if tenant_id in self._usage:
                count += len(self._usage[tenant_id])
                del self._usage[tenant_id]
            return count

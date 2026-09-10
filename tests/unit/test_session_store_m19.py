import time
import threading
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.history import ConversationHistory
from core.session_store import FileSessionStore, InMemorySessionStore
from core.session_types import SessionContext, SessionMetadata, SessionStatus


def test_in_memory_session_store_crud():
    store = InMemorySessionStore()
    assert store.count() == 0

    meta = SessionMetadata(session_id="sess_1", user_id="alice")
    ctx = SessionContext(metadata=meta)
    store.save(ctx)

    assert store.count() == 1
    retrieved = store.get("sess_1")
    assert retrieved is not None
    assert retrieved.session_id == "sess_1"
    assert retrieved.metadata.user_id == "alice"

    # Verify isolation
    retrieved.active_goal_ids.append("goal_1")
    # Fresh get should not have goal_1 unless saved
    fresh = store.get("sess_1")
    assert fresh.active_goal_ids == []

    # List sessions
    sessions = store.list_sessions(user_id="alice")
    assert len(sessions) == 1
    assert sessions[0].session_id == "sess_1"

    # Delete
    assert store.delete("sess_1") is True
    assert store.get("sess_1") is None
    assert store.count() == 0
    assert store.delete("sess_1") is False


def test_in_memory_session_store_prune():
    store = InMemorySessionStore()
    now = time.time()
    ctx1 = SessionContext(
        metadata=SessionMetadata(
            session_id="s1",
            last_accessed_at=now - 500,
            ttl_seconds=100.0,
        )
    )
    ctx2 = SessionContext(
        metadata=SessionMetadata(
            session_id="s2",
            last_accessed_at=now,
            ttl_seconds=100.0,
        )
    )
    store.save(ctx1)
    store.save(ctx2)

    pruned = store.prune_expired_sessions(current_time=now)
    assert pruned == 1
    assert store.get("s1").status == SessionStatus.EXPIRED
    assert store.get("s2").status == SessionStatus.ACTIVE


def test_file_session_store_atomic_persistence():
    with TemporaryDirectory() as tmp_dir:
        store = FileSessionStore(tmp_dir)
        meta = SessionMetadata(session_id="file_sess_1", user_id="bob")
        history = ConversationHistory()
        history.add_turn(user_input="hello disk", assistant_output="hello memory")
        ctx = SessionContext(
            metadata=meta,
            history=history,
            active_goal_ids=["g_disk"],
            active_task_ids=["t_disk"],
        )

        store.save(ctx)
        assert store.count() == 1

        retrieved = store.get("file_sess_1")
        assert retrieved is not None
        assert retrieved.session_id == "file_sess_1"
        assert retrieved.metadata.user_id == "bob"
        assert len(retrieved.history.turns) == 1
        assert retrieved.history.turns[0].user_input == "hello disk"
        assert retrieved.active_goal_ids == ["g_disk"]

        # List
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "file_sess_1"

        # Delete
        assert store.delete("file_sess_1") is True
        assert store.get("file_sess_1") is None
        assert store.count() == 0


def test_file_session_store_corrupted_file_resilience():
    with TemporaryDirectory() as tmp_dir:
        store = FileSessionStore(tmp_dir)
        corrupted_file = Path(tmp_dir) / "corrupted_sess.json"
        corrupted_file.write_text("{invalid json truncated ...", encoding="utf-8")

        # Getting the corrupted session should return None and quarantine the file
        retrieved = store.get("corrupted_sess")
        assert retrieved is None

        # Listing should not crash
        sessions = store.list_sessions()
        assert len(sessions) == 0


def test_file_session_store_prune_expired():
    with TemporaryDirectory() as tmp_dir:
        store = FileSessionStore(tmp_dir)
        now = time.time()
        ctx1 = SessionContext(
            metadata=SessionMetadata(
                session_id="exp_sess",
                last_accessed_at=now - 1000,
                ttl_seconds=100.0,
            )
        )
        ctx2 = SessionContext(
            metadata=SessionMetadata(
                session_id="act_sess",
                last_accessed_at=now,
                ttl_seconds=100.0,
            )
        )
        store.save(ctx1)
        store.save(ctx2)

        pruned = store.prune_expired_sessions(current_time=now)
        assert pruned == 1
        assert store.get("exp_sess").status == SessionStatus.EXPIRED
        assert store.get("act_sess").status == SessionStatus.ACTIVE


def test_file_session_store_concurrent_writes():
    with TemporaryDirectory() as tmp_dir:
        store = FileSessionStore(tmp_dir)
        errors = []

        def worker(worker_id: int):
            try:
                for i in range(10):
                    sid = f"worker_{worker_id}_sess_{i}"
                    ctx = SessionContext(metadata=SessionMetadata(session_id=sid, user_id=f"user_{worker_id}"))
                    store.save(ctx)
                    read_ctx = store.get(sid)
                    if read_ctx is None or read_ctx.session_id != sid:
                        errors.append(f"Mismatch in worker {worker_id} for session {sid}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert store.count() == 50

"""R-F1002 — Tests for Multi-User OS."""
from __future__ import annotations

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock


class TestMultiUserOS:
    """Test the Multi-User Operating System."""

    def test_register_user(self):
        """register_user should create a user."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        user = os.register_user("test_user", "Test User", role="admin")
        assert user.id == "test_user"
        assert user.name == "Test User"
        assert user.role == "admin"

    def test_get_user(self):
        """get_user should return the user."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        os.register_user("test_user")
        user = os.get_user("test_user")
        assert user is not None
        assert user.id == "test_user"

    def test_get_user_not_found(self):
        """get_user should return None for unknown users."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        assert os.get_user("nonexistent") is None

    def test_create_team(self):
        """create_team should create a team."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        team = os.create_team("test_team", "Test Team")
        assert team.id == "test_team"
        assert team.name == "Test Team"

    def test_submit_task(self):
        """submit_task should create a queued task."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        task = os.submit_task("chat", "Test task", user_id="admin")
        assert task.id is not None
        assert task.status.value == "queued"
        assert task.description == "Test task"

    def test_submit_task_rate_limit(self):
        """submit_task should enforce rate limits."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        user = os.register_user("limited_user")
        user.rate_limit = 1
        os.submit_task("chat", "First task", user_id="limited_user")
        with pytest.raises(ValueError, match="Rate limit exceeded"):
            os.submit_task("chat", "Second task", user_id="limited_user")

    def test_get_task(self):
        """get_task should return the task."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        task = os.submit_task("chat", "Test task")
        retrieved = os.get_task(task.id)
        assert retrieved is not None
        assert retrieved.id == task.id

    def test_get_user_tasks(self):
        """get_user_tasks should return tasks for a user."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        os.submit_task("chat", "Task 1", user_id="admin")
        os.submit_task("research", "Task 2", user_id="admin")
        tasks = os.get_user_tasks("admin")
        assert len(tasks) == 2

    def test_update_task_progress(self):
        """update_task_progress should update the progress."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        task = os.submit_task("chat", "Test task")
        os.update_task_progress(task.id, "Processing...")
        assert os.get_task(task.id).progress == "Processing..."

    def test_complete_task(self):
        """complete_task should mark task as completed."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        task = os.submit_task("chat", "Test task")
        os.complete_task(task.id, {"result": "success"})
        assert os.get_task(task.id).status.value == "completed"
        assert os.get_task(task.id).result == {"result": "success"}

    def test_fail_task(self):
        """fail_task should mark task as failed."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        task = os.submit_task("chat", "Test task")
        os.fail_task(task.id, "Something went wrong")
        assert os.get_task(task.id).status.value == "failed"
        assert os.get_task(task.id).error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_worker_processes_tasks(self):
        """The worker should process queued tasks."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()

        # Override _process_task to complete immediately
        async def fast_process(task):
            os.complete_task(task.id, {"result": "done"})
        os._process_task = fast_process

        task = os.submit_task("chat", "Test task")
        await os.start_worker()
        await asyncio.sleep(0.2)
        await os.stop_worker()

        assert os.get_task(task.id).status.value == "completed"

    @pytest.mark.asyncio
    async def test_concurrent_tasks(self):
        """Multiple tasks should be processed concurrently."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        os._max_concurrent = 3

        processed = []

        async def track_process(task):
            await asyncio.sleep(0.1)
            processed.append(task.id)
            os.complete_task(task.id, {"result": "done"})
        os._process_task = track_process

        tasks = []
        for i in range(5):
            t = os.submit_task("chat", f"Task {i}")
            tasks.append(t)

        await os.start_worker()
        await asyncio.sleep(1.0)
        await os.stop_worker()

        assert len(processed) == 5

    def test_get_status(self):
        """get_status should return system status."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        status = os.get_status()
        assert "users" in status
        assert "teams" in status
        assert "tasks" in status
        assert "active_workers" in status

    def test_team_tasks(self):
        """get_team_tasks should return tasks for a team."""
        from aria_service.intel.multi_user_os import MultiUserOS
        os = MultiUserOS()
        team = os.create_team("team_a", "Team A")
        os.register_user("user_a", team_id="team_a")
        os.submit_task("chat", "Team task", user_id="user_a")
        tasks = os.get_team_tasks("team_a")
        assert len(tasks) == 1


class TestTaskBroadcaster:
    """Test the task broadcaster."""

    def test_register_channel(self):
        """register_channel should add a handler."""
        from aria_service.intel.multi_user_os import MultiUserOS, TaskBroadcaster
        os = MultiUserOS()
        broadcaster = TaskBroadcaster(os)
        handler = lambda s: None
        broadcaster.register_channel("web", handler)
        assert len(broadcaster._channel_handlers["web"]) == 1

    def test_format_for_display(self):
        """format_for_display should return a formatted string."""
        from aria_service.intel.multi_user_os import TaskBroadcaster
        os = type("obj", (object,), {"on_status_change": lambda self, cb: None})()
        broadcaster = TaskBroadcaster(os)
        status = {
            "task_id": "task_abc123",
            "description": "Test task description",
            "status": "running",
            "type": "chat",
            "user_id": "admin",
            "progress": "Processing...",
            "channel": "cli",
        }
        formatted = broadcaster.format_for_display(status)
        assert "Test task" in formatted
        assert "running" in formatted

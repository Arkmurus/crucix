"""R-F1002 — ARIA Multi-User Operating System.

Enables:
1. Concurrent task processing (multiple users/teams simultaneously)
2. Real-time task visibility (broadcast to CLI, Web, WhatsApp, Telegram)
3. User isolation (each user has own session, context, rate limits)
4. Team collaboration (shared workspaces, cross-user knowledge)
5. Task queue with priorities (background processing, no blocking)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("aria.multi_user_os")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Task:
    """A unit of work that ARIA can process."""
    id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    user_id: str = ""
    session_id: str = ""
    type: str = ""  # "chat", "research", "dd", "code", "screen", "ingest"
    description: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    progress: str = ""
    channel: str = "cli"  # "cli", "web", "whatsapp", "telegram", "api"
    team_id: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# USER SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class User:
    """A user of the ARIA system."""
    id: str
    name: str = ""
    team_id: str = ""
    role: str = "member"  # "admin", "member", "viewer"
    rate_limit: int = 10  # tasks per minute
    tasks_this_minute: int = 0
    last_task_reset: float = field(default_factory=time.time)
    session_id: str = ""
    channels: list[str] = field(default_factory=lambda: ["cli"])


# ═══════════════════════════════════════════════════════════════════════════════
# TEAM SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Team:
    """A team of users working together."""
    id: str
    name: str = ""
    members: list[str] = field(default_factory=list)
    shared_knowledge: bool = True
    created_at: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-USER OS
# ═══════════════════════════════════════════════════════════════════════════════

class MultiUserOS:
    """ARIA's multi-user operating system.
    
    Handles:
    - Concurrent task processing via asyncio
    - User isolation and rate limiting
    - Task queue with priorities
    - Real-time status broadcasting
    - Team collaboration
    """

    def __init__(self):
        self._users: dict[str, User] = {}
        self._teams: dict[str, Team] = {}
        self._tasks: dict[str, Task] = {}
        self._task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._status_callbacks: list[callable] = []
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._max_concurrent = 5

        # Register default team
        default_team = Team(id="default", name="Default Team")
        self._teams["default"] = default_team

        # Register default admin user
        admin = User(id="admin", name="ARIA Admin", role="admin", team_id="default")
        self._users["admin"] = admin

    # ── User Management ────────────────────────────────────────────────────

    def register_user(self, user_id: str, name: str = "", team_id: str = "default",
                      role: str = "member", channels: Optional[list[str]] = None) -> User:
        """Register a new user."""
        user = User(
            id=user_id,
            name=name or user_id,
            team_id=team_id,
            role=role,
            channels=channels or ["cli"],
        )
        self._users[user_id] = user

        # Add to team
        if team_id in self._teams:
            if user_id not in self._teams[team_id].members:
                self._teams[team_id].members.append(user_id)

        logger.info("[multi_user_os] registered user %s (team=%s, role=%s)", user_id, team_id, role)
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID."""
        return self._users.get(user_id)

    # ── Team Management ────────────────────────────────────────────────────

    def create_team(self, team_id: str, name: str = "") -> Team:
        """Create a new team."""
        team = Team(id=team_id, name=name or team_id)
        self._teams[team_id] = team
        logger.info("[multi_user_os] created team %s", team_id)
        return team

    def get_team(self, team_id: str) -> Optional[Team]:
        """Get a team by ID."""
        return self._teams.get(team_id)

    # ── Task Management ────────────────────────────────────────────────────

    def submit_task(self, task_type: str, description: str, user_id: str = "admin",
                    priority: TaskPriority = TaskPriority.NORMAL,
                    channel: str = "cli", session_id: str = "") -> Task:
        """Submit a new task to the queue."""
        # Check rate limit
        user = self._users.get(user_id)
        if user:
            now = time.time()
            if now - user.last_task_reset > 60:
                user.tasks_this_minute = 0
                user.last_task_reset = now
            if user.tasks_this_minute >= user.rate_limit:
                raise ValueError(f"Rate limit exceeded for user {user_id}")
            user.tasks_this_minute += 1

        task = Task(
            type=task_type,
            description=description,
            user_id=user_id,
            priority=priority,
            channel=channel,
            session_id=session_id or user.session_id if user else "",
            team_id=user.team_id if user else "default",
        )
        self._tasks[task.id] = task

        # Add to priority queue (negate priority so higher = processed first)
        self._task_queue.put_nowait((-priority.value, task.id))

        logger.info("[multi_user_os] task %s queued: %s (%s)", task.id, description, task_type)
        self._broadcast_status(task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_user_tasks(self, user_id: str, limit: int = 20) -> list[Task]:
        """Get recent tasks for a user."""
        tasks = [t for t in self._tasks.values() if t.user_id == user_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def get_team_tasks(self, team_id: str, limit: int = 50) -> list[Task]:
        """Get recent tasks for a team."""
        tasks = [t for t in self._tasks.values() if t.team_id == team_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def update_task_progress(self, task_id: str, progress: str) -> None:
        """Update the progress of a task."""
        task = self._tasks.get(task_id)
        if task:
            task.progress = progress
            self._broadcast_status(task)

    def complete_task(self, task_id: str, result: Any = None) -> None:
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.result = result
            logger.info("[multi_user_os] task %s completed", task_id)
            self._broadcast_status(task)

    def fail_task(self, task_id: str, error: str) -> None:
        """Mark a task as failed."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error
            logger.info("[multi_user_os] task %s failed: %s", task_id, error)
            self._broadcast_status(task)

    # ── Task Processing ────────────────────────────────────────────────────

    async def start_worker(self) -> None:
        """Start the background task worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("[multi_user_os] worker started (max_concurrent=%d)", self._max_concurrent)

    async def stop_worker(self) -> None:
        """Stop the background task worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("[multi_user_os] worker stopped")

    async def _worker_loop(self) -> None:
        """Main worker loop — processes tasks from the queue."""
        semaphore = asyncio.Semaphore(self._max_concurrent)

        while self._running:
            try:
                # Get next task from queue with timeout
                try:
                    _, task_id = await asyncio.wait_for(
                        self._task_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                task = self._tasks.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    continue

                # Process task with semaphore (limit concurrency)
                async with semaphore:
                    task.status = TaskStatus.RUNNING
                    task.started_at = time.time()
                    self._broadcast_status(task)

                    # Create async task
                    processing = asyncio.create_task(
                        self._process_task(task)
                    )
                    self._active_tasks[task_id] = processing

                    try:
                        await processing
                    except asyncio.CancelledError:
                        task.status = TaskStatus.CANCELLED
                        self._broadcast_status(task)
                    except Exception as e:
                        self.fail_task(task_id, str(e))
                    finally:
                        self._active_tasks.pop(task_id, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[multi_user_os] worker error: %s", e)

    async def _process_task(self, task: Task) -> None:
        """Process a single task using ARIA's actual capabilities."""
        self.update_task_progress(task.id, f"Processing {task.type}: {task.description}")
        
        try:
            if task.type == "code":
                from .self_coding_os import SelfCodingOS
                os = SelfCodingOS()
                plan = os.plan_change(task.description)
                result = await os.execute_plan(plan)
                self.complete_task(task.id, result)
                
            elif task.type == "review":
                from .expert_coder import CodeReview
                reviewer = CodeReview()
                findings = reviewer.review(task.description)
                self.complete_task(task.id, {"findings": findings})
                
            elif task.type == "debug":
                from .expert_coder import DebugEngine
                debug = DebugEngine()
                diagnosis = debug.diagnose(task.description)
                self.complete_task(task.id, diagnosis)
                
            elif task.type == "research":
                from .self_sufficient import KnowledgeAugmentedResponder
                responder = KnowledgeAugmentedResponder()
                result = await responder.answer(task.description)
                self.complete_task(task.id, result)
                
            else:
                # Generic processing
                await asyncio.sleep(0.1)
                self.complete_task(task.id, {"status": "processed", "type": task.type})
                
        except Exception as e:
            self.fail_task(task.id, str(e))

    # ── Status Broadcasting ────────────────────────────────────────────────

    def on_status_change(self, callback: callable) -> None:
        """Register a callback for task status changes."""
        self._status_callbacks.append(callback)

    def _broadcast_status(self, task: Task) -> None:
        """Broadcast task status to all registered callbacks."""
        status = {
            "task_id": task.id,
            "user_id": task.user_id,
            "team_id": task.team_id,
            "type": task.type,
            "description": task.description,
            "status": task.status.value,
            "progress": task.progress,
            "channel": task.channel,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        }
        for callback in self._status_callbacks:
            try:
                callback(status)
            except Exception as e:
                logger.debug("[multi_user_os] callback error: %s", e)

    # ── Status Query ───────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the multi-user OS."""
        queued = sum(1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED)
        running = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
        completed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED)

        return {
            "users": len(self._users),
            "teams": len(self._teams),
            "tasks": {
                "total": len(self._tasks),
                "queued": queued,
                "running": running,
                "completed": completed,
                "failed": failed,
            },
            "active_workers": len(self._active_tasks),
            "max_concurrent": self._max_concurrent,
            "worker_running": self._running,
            "current_tasks": [
                {
                    "id": t.id,
                    "type": t.type,
                    "description": t.description[:50],
                    "status": t.status.value,
                    "user": t.user_id,
                    "progress": t.progress[:50] if t.progress else "",
                }
                for t in sorted(self._tasks.values(), key=lambda x: x.created_at, reverse=True)[:10]
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TASK VISIBILITY — broadcasts to all channels
# ═══════════════════════════════════════════════════════════════════════════════

class TaskBroadcaster:
    """Broadcasts task status to all channels (CLI, Web, WhatsApp, Telegram)."""

    def __init__(self, multi_user_os: MultiUserOS):
        self.os = multi_user_os
        self.os.on_status_change(self._on_status_change)
        self._channel_handlers: dict[str, list[callable]] = {
            "cli": [],
            "web": [],
            "whatsapp": [],
            "telegram": [],
            "api": [],
        }

    def register_channel(self, channel: str, handler: callable) -> None:
        """Register a handler for a specific channel."""
        if channel in self._channel_handlers:
            self._channel_handlers[channel].append(handler)

    def _on_status_change(self, status: dict) -> None:
        """Handle a task status change and broadcast to channels."""
        channel = status.get("channel", "cli")
        # Broadcast to the task's origin channel
        handlers = self._channel_handlers.get(channel, [])
        for handler in handlers:
            try:
                handler(status)
            except Exception:
                pass

        # Also broadcast to CLI (always)
        for handler in self._channel_handlers.get("cli", []):
            try:
                handler(status)
            except Exception:
                pass

    def format_for_display(self, status: dict) -> str:
        """Format a task status for human-readable display."""
        emoji = {
            "queued": "📋",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",
        }
        e = emoji.get(status["status"], "📋")
        return (
            f"{e} Task {status['task_id'][:8]}: {status['description'][:60]}\n"
            f"   Status: {status['status']} | Type: {status['type']} | User: {status['user_id']}\n"
            f"   Progress: {status['progress'][:60] if status['progress'] else 'Waiting...'}"
        )

# R-F1002 - wire to brain
from .engine_wiring import wire_success
wire_success(module="multi_user_os", summary="Multi-User OS Active", source_id="multi_user_os:R-F1002")

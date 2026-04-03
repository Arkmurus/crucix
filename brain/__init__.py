"""
CRUCIX Autonomous Brain
Drop-in intelligence layer for the Crucix OSINT Platform.
"""
from .autonomous_agent import CrucixAutonomousAgent
from .config import CONFIG, BrainConfig
from .deepseek_client import DeepSeekClient
from .feedback_loop import FeedbackLoop
from .memory import CrucixMemory
from .ml_engine import CrucixMLEngine

__version__ = "1.0.0"
__all__ = [
    "CrucixAutonomousAgent",
    "CrucixMemory",
    "CrucixMLEngine",
    "DeepSeekClient",
    "FeedbackLoop",
    "CONFIG",
    "BrainConfig",
]

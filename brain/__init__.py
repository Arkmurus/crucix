"""
CRUCIX Autonomous Brain + ARIA
Drop-in intelligence layer for the Crucix OSINT Platform.
"""
from .autonomous_agent import CrucixAutonomousAgent
from .config import CONFIG, BrainConfig
from .deepseek_client import DeepSeekClient
from .feedback_loop import FeedbackLoop
from .memory import CrucixMemory
from .ml_engine import CrucixMLEngine
from .aria_cognition import ARIACognition
from .aria_chat import ARIAChat

__version__ = "2.0.0"
__all__ = [
    "CrucixAutonomousAgent",
    "CrucixMemory",
    "CrucixMLEngine",
    "DeepSeekClient",
    "FeedbackLoop",
    "ARIACognition",
    "ARIAChat",
    "CONFIG",
    "BrainConfig",
]

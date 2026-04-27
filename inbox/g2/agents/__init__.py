"""All G2 agents. Import the individual modules to get the entry coroutines."""

from .calendar import calendar_agent
from .context import context_agent
from .messages import messages_agent
from .reminders import reminders_agent
from .transcript import transcript_agent
from .voice_actions import voice_actions_agent

__all__ = [
    "calendar_agent",
    "context_agent",
    "messages_agent",
    "reminders_agent",
    "transcript_agent",
    "voice_actions_agent",
]

"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .content_writer import CONTENT_WRITER_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .proofreader import PROOFREADER_CONFIG
from .story_planner import STORY_PLANNER_CONFIG

__all__ = [
    "BASH_AGENT_CONFIG",
    "CONTENT_WRITER_CONFIG",
    "GENERAL_PURPOSE_CONFIG",
    "PROOFREADER_CONFIG",
    "STORY_PLANNER_CONFIG",
]

BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "story-planner": STORY_PLANNER_CONFIG,
    "content-writer": CONTENT_WRITER_CONFIG,
    "proofreader": PROOFREADER_CONFIG,
}

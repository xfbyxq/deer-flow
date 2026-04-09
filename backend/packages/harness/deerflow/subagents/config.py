"""Subagent configuration definitions."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SubagentConfig:
    """Configuration for a subagent.

    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum number of agent turns before stopping.
        timeout_seconds: Maximum execution time in seconds (default: 900 = 15 minutes).
        role: Role type for team composition (e.g., 'planner', 'writer', 'reviewer').
        team: Team name this agent belongs to.
        next_agents: List of agents to call after this agent completes (for sequential workflow).
    """

    name: str
    description: str
    system_prompt: str
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900
    role: Literal["planner", "writer", "reviewer", "coordinator", "general"] | None = None
    team: str | None = None
    next_agents: list[str] | None = None

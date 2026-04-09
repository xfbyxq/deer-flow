"""智能体团队（多智能体组合）配置。"""

import logging
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentTeamConfig(BaseModel):
    """智能体团队配置。"""

    team_name: str = Field(description="团队唯一标识符")
    description: str = Field(default="", description="团队描述")
    lead_agent: str = Field(description="团队的主协调智能体")
    members: list[str] = Field(default_factory=list, description="团队成员智能体名称列表")
    workflow: Literal["sequential", "parallel", "conditional"] = Field(
        default="sequential",
        description="团队协作方式：sequential（顺序执行）、parallel（并行执行）、conditional（条件分支）",
    )


class TeamsAppConfig(BaseModel):
    """所有智能体团队的配置。"""

    teams: dict[str, AgentTeamConfig] = Field(
        default_factory=dict,
        description="按团队名称键控的智能体团队",
    )


_teams_config: TeamsAppConfig = TeamsAppConfig()


def get_teams_app_config() -> TeamsAppConfig:
    """获取当前的团队配置。"""
    return _teams_config


def load_teams_config_from_dict(config_dict: dict) -> None:
    """从字典加载团队配置。"""
    global _teams_config
    _teams_config = TeamsAppConfig(**config_dict)

    for team_name, team in _teams_config.teams.items():
        logger.info(
            "已加载团队 '%s'：lead_agent=%s，members=%s，workflow=%s",
            team_name,
            team.lead_agent,
            team.members,
            team.workflow,
        )


def get_team_config(team_name: str) -> AgentTeamConfig | None:
    """根据名称获取特定的团队配置。"""
    return _teams_config.teams.get(team_name)


def list_teams() -> list[str]:
    """列出所有可用团队名称。"""
    return list(_teams_config.teams.keys())

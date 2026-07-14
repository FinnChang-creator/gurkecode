"""prompt 包 — 系统提示工程化

提供模块化系统提示拼装、环境信息采集、补充消息构造。
"""

from prompt.builder import (
    PromptBuilder,
    Section,
    create_default_sections,
)
from prompt.environment import (
    EnvironmentInfo,
    collect_environment,
    format_environment,
)
from prompt.reminder import (
    ReminderBuilder,
    ReminderState,
)

__all__ = [
    # builder
    "Section",
    "PromptBuilder",
    "create_default_sections",
    # environment
    "EnvironmentInfo",
    "collect_environment",
    "format_environment",
    # reminder
    "ReminderState",
    "ReminderBuilder",
]

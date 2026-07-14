"""补充消息构造器

负责构造 '<system-reminder>' 格式的补充消息，
并控制计划模式提醒的注入频率（首轮完整 → 间隔 N 轮完整 → 其余精简）。

注入的 reminder 消息以 role="user" 身份插入当前轮消息列表，
不写入持久对话历史（F6：不污染缓存、不破坏历史）。
"""

from protocol.models import ChatMessage

# ---------------------------------------------------------------------------
# 计划模式提醒文本
# ---------------------------------------------------------------------------

_FULL_PLAN_REMINDER = """\
You are in **PLAN MODE**. In this mode:
- You have access to **read-only tools** (read_file, glob_search, grep_search) \
to explore the codebase and understand the current implementation.
- You CANNOT make any changes — no writing files, editing files, or executing commands.
- Your goal is to understand the user's request, explore relevant code, and produce a \
**detailed implementation plan**.
- Your plan should include: files to create/modify, specific changes needed, \
the order of operations, and any architectural decisions or trade-offs.
- Be thorough and concrete — reference actual file paths and line numbers from \
your exploration.
- Once your plan is complete, tell the user they can switch to execution mode \
with `/do`."""

_BRIEF_PLAN_REMINDER = (
    "Still in PLAN MODE — read-only tools only. "
    "Continue exploring or finalize your plan and ask the user to run `/do`."
)

# ---------------------------------------------------------------------------
# Reminder 包装函数
# ---------------------------------------------------------------------------


def _wrap_reminder(content: str) -> str:
    """将指令文本包装为 <system-reminder> 格式。

    Args:
        content: 原始指令文本

    Returns:
        包装后的消息文本
    """
    return f"<system-reminder>\n{content}\n</system-reminder>"


# ---------------------------------------------------------------------------
# ReminderState — 注入状态跟踪
# ---------------------------------------------------------------------------


class ReminderState:
    """跟踪 system-reminder 的注入轮次和频率。

    用于 F7（规划模式按轮次注入）：
    - 首轮注入完整提醒
    - 之后每隔 interval 轮重复完整
    - 其余轮次注入精简版

    Attributes:
        iteration: 当前轮次计数（每次 Agent Loop 迭代 +1）
        interval: 完整提醒间隔轮次，默认 3
    """

    def __init__(self, interval: int = 3) -> None:
        """初始化提醒状态。

        Args:
            interval: 完整提醒间隔轮次，默认每 3 轮注入一次完整提醒
        """
        self.iteration: int = 0
        self.interval: int = interval


# ---------------------------------------------------------------------------
# ReminderBuilder — 消息构造
# ---------------------------------------------------------------------------


class ReminderBuilder:
    """根据当前状态构造 <system-reminder> 消息。

    支持两种提醒类型：
    - 计划模式提醒（build_plan_reminder）
    - 通用补充指令（build_generic_reminder）

    使用方式：
        state = ReminderState()
        builder = ReminderBuilder(state)
        msg = builder.build_plan_reminder()  # ChatMessage 或 None
    """

    def __init__(self, state: ReminderState) -> None:
        """初始化构造器。

        Args:
            state: 提醒状态跟踪器，builder 持有其引用
        """
        self._state = state

    def build_plan_reminder(self) -> ChatMessage:
        """构造计划模式提醒消息。

        根据当前轮次选择完整版或精简版文本，
        包装为 <system-reminder> 格式的 ChatMessage。

        频率控制逻辑：
        - iteration == 0         → 完整（首轮）
        - iteration % interval == 0 → 完整
        - 其他                    → 精简

        Returns:
            ChatMessage(role="user", content="<system-reminder>...")
        """
        if (self._state.iteration == 0
                or self._state.iteration % self._state.interval == 0):
            text = _FULL_PLAN_REMINDER
        else:
            text = _BRIEF_PLAN_REMINDER

        return ChatMessage(
            role="user",
            content=_wrap_reminder(text),
        )

    def build_generic_reminder(self, content: str) -> ChatMessage:
        """构造通用补充指令消息。

        用于 /do 触发等非计划模式的补充指令场景。
        同样包装为 <system-reminder> 格式。

        Args:
            content: 要注入的指令文本

        Returns:
            ChatMessage(role="user", content="<system-reminder>...")
        """
        return ChatMessage(
            role="user",
            content=_wrap_reminder(content),
        )

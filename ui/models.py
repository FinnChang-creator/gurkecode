"""UI 层数据模型

定义界面展示所需的数据类型，与协议层的 ChatMessage 区分开：
- UI 层消息有角色标识和展示状态（流式/完成/错误）
- 这些类型供 ChatView 等 widget 使用
"""

from dataclasses import dataclass, field
from enum import Enum


class MessageRole(str, Enum):
    """消息在界面上的角色分类。

    决定每条消息在对话区的样式（颜色、对齐、图标等）。
    """

    USER = "user"        # 用户输入的消息
    ASSISTANT = "assistant"  # 助手的回复
    ERROR = "error"       # 错误信息
    SYSTEM = "system"     # 系统提示（如就绪信息、退出提示）


class MessageState(str, Enum):
    """消息的展示状态。

    决定消息是静态展示还是动态更新。
    """

    STREAMING = "streaming"   # 流式传输中：逐字更新，显示为纯文本
    COMPLETE = "complete"     # 已完成：定型展示，可切换为 markdown 渲染
    ERROR = "error"           # 错误状态：以可区分的样式展示


@dataclass
class DisplayMessage:
    """对话区中一条消息的完整展示数据。

    Attributes:
        id: 唯一标识，用于更新已存在的消息（流式期间替换内容）
        role: 消息角色，决定外观样式
        content: 当前文本内容
        state: 展示状态
        timestamp: 消息创建时间（Unix 时间戳），用于排序
    """

    id: str
    role: MessageRole
    content: str = ""
    state: MessageState = MessageState.COMPLETE
    timestamp: float = field(default_factory=lambda: __import__("time").time())

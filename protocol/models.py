"""协议层数据模型

定义协议适配层的核心数据结构和抽象接口。
上层（ChatEngine 和 UI）只依赖这些类型，不需要关心具体协议实现。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import AsyncIterator


@dataclass
class ChatMessage:
    """一条对话消息。

    Attributes:
        role: 消息角色——"system"（系统提示）、"user"（用户）、"assistant"（助手回复）
        content: 消息正文文本
    """

    role: str
    content: str


@dataclass
class StreamEvent:
    """流式响应中的一个事件。

    协议适配器将 SSE 流中的各个增量转换为统一的 StreamEvent 序列，
    供 ChatEngine 消费。

    Attributes:
        kind: 事件类型——
            "text_delta"   — 一个正文文本增量片段
            "thinking_delta" — 扩展思考增量（仅 Anthropic，引擎应丢弃）
            "done"         — 本轮响应正常结束
            "error"        — 发生错误，error 字段包含描述
        text: 增量文本内容（仅 text_delta 类型时有效）
        error: 错误描述（仅 error 类型时有效）
    """

    kind: str  # "text_delta" | "thinking_delta" | "done" | "error"
    text: str = ""
    error: str = ""

    # 事件类型常量，避免调用方硬编码字符串
    KIND_TEXT_DELTA = "text_delta"
    KIND_THINKING_DELTA = "thinking_delta"
    KIND_DONE = "done"
    KIND_ERROR = "error"


class ChatProtocol(ABC):
    """协议适配器抽象基类。

    定义了与 LLM 服务商通信的统一接口。
    每个具体实现（AnthropicProtocol、OpenAIProtocol）负责：
    - 按对应协议构造 HTTP 请求体
    - 发起流式请求
    - 解析 SSE 响应，产出统一的 StreamEvent 序列

    上层调用方只需要拿到一个 ChatProtocol 实例，
    调用 chat() 并遍历其返回的异步迭代器即可。
    """

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """返回协议名称标识，如 "anthropic" 或 "openai"。

        用于日志记录、错误消息中的来源标识等。
        """
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        thinking: bool,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求。

        Args:
            messages: 完整对话上下文（system + user + assistant 历史）
            model: 要使用的模型名称
            thinking: 是否开启扩展思考（仅 Anthropic 协议有效，OpenAI 忽略）

        Yields:
            StreamEvent: 流式事件序列，依次产出 text_delta/thinking_delta/done/error

        Note:
            这是一个异步生成器方法，调用方用 async for 遍历。
            实现方应在检测到流结束时产出 KIND_DONE 事件、
            或在捕获异常时产出 KIND_ERROR 事件。
        """
        ...

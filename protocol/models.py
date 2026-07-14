"""协议层数据模型

定义协议适配层的核心数据结构和抽象接口。
上层（ChatEngine 和 UI）只依赖这些类型，不需要关心具体协议实现。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import AsyncIterator


@dataclass
class ToolCall:
    """模型发起的一次工具调用请求。

    由协议层从 SSE 流中解析出的工具调用信息，
    包含调用 ID、工具名称和已解析的参数对象。

    Attributes:
        id: 调用唯一标识（由模型生成，用于回灌时关联结果）
        name: 工具名称（如 "read_file"、"bash" 等）
        arguments: 已解析的 JSON 参数字典
    """

    id: str
    name: str
    arguments: dict


@dataclass
class ChatMessage:
    """一条对话消息。

    扩展了工具调用支持：
    - role="assistant" 时可携带 tool_calls 列表表示模型请求调用工具
    - role="tool" 时携带 tool_result 表示工具执行结果回灌

    Attributes:
        role: 消息角色——
            "system"（系统提示）、"user"（用户）、
            "assistant"（助手回复，可能含工具调用）、
            "tool"（工具执行结果）
        content: 消息正文文本（tool 角色时可能为空）
        tool_calls: 模型请求的工具调用列表（仅 assistant 角色）
        tool_call_id: 工具调用 ID（仅 tool 角色，回灌时关联）
        name: 工具名称（仅 tool 角色）
    """

    role: str
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class Usage:
    """一次 LLM 请求的 token 用量，含缓存命中信息。

    从 provider 响应中的 usage 字段提取。
    缓存字段为 0 时表示未命中或该 provider 不支持缓存。

    Attributes:
        input_tokens: 本次请求的输入 token 数
        output_tokens: 本次请求的输出 token 数
        cache_read_tokens: 从缓存中读取（命中）的 token 数，0 表示未命中
        cache_write_tokens: 新写入缓存的 token 数，0 表示未创建或已存在
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class StreamEvent:
    """流式响应中的一个事件。

    协议适配器将 SSE 流中的各个增量转换为统一的 StreamEvent 序列，
    供 ChatEngine 消费。

    Attributes:
        kind: 事件类型——
            "text_delta"        — 一个正文文本增量片段
            "thinking_delta"    — 扩展思考增量（仅 Anthropic，引擎应丢弃）
            "tool_call_start"   — 开始解析一个工具调用（携带 id 和 name）
            "tool_call_delta"   — 工具调用 JSON 参数增量片段
            "tool_call_end"     — 工具调用参数解析完成（携带完整 arguments）
            "done"              — 本轮响应正常结束
            "error"             — 发生错误，error 字段包含描述
        text: 增量文本内容（text_delta / tool_call_delta 类型时有效）
        error: 错误描述（仅 error 类型时有效）
        tool_call_id: 工具调用唯一 ID（tool_call_* 类型时有效）
        tool_call_name: 工具名称（tool_call_start / tool_call_end 时有效）
        tool_arguments: 已解析的完整参数字典（仅 tool_call_end 时有效）
    """

    kind: str
    text: str = ""
    error: str = ""
    tool_call_id: str = ""
    tool_call_name: str = ""
    tool_arguments: dict | None = None
    usage: "Usage | None" = None

    # 事件类型常量，避免调用方硬编码字符串
    KIND_TEXT_DELTA = "text_delta"
    KIND_THINKING_DELTA = "thinking_delta"
    KIND_TOOL_CALL_START = "tool_call_start"
    KIND_TOOL_CALL_DELTA = "tool_call_delta"
    KIND_TOOL_CALL_END = "tool_call_end"
    KIND_TOOL_EXECUTED = "tool_executed"
    KIND_DONE = "done"
    KIND_USAGE = "usage"
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
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求。

        Args:
            messages: 完整对话上下文（system + user + assistant 历史，
                      可能包含 tool_calls 和 tool 角色消息）
            model: 要使用的模型名称
            thinking: 是否开启扩展思考（仅 Anthropic 协议有效，OpenAI 忽略）
            tools: 可选工具定义列表（协议无关中间格式），
                   为 None 时不发送工具定义

        Yields:
            StreamEvent: 流式事件序列，依次产出
                         text_delta / thinking_delta /
                         tool_call_start / tool_call_delta / tool_call_end /
                         done / error

        Note:
            这是一个异步生成器方法，调用方用 async for 遍历。
            实现方应在检测到流结束时产出 KIND_DONE 事件、
            或在捕获异常时产出 KIND_ERROR 事件。
        """
        ...

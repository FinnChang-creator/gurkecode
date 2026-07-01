"""对话引擎

维护多轮对话的上下文历史，编排每次请求，分发流式事件，管理响应计时。

ChatEngine 是 UI 和 Protocol 之间的桥梁：
- UI 调用 add_user_message() 记录用户输入
- UI 遍历 stream_response() 获取流式事件并驱动界面更新
- ChatEngine 负责组装 system prompt + history、调用 protocol.chat()、
  过滤 thinking 内容、记录 assistant 回复到历史
"""

import time
from collections.abc import AsyncIterator

from config.models import ProviderConfig
from protocol.models import ChatMessage, ChatProtocol, StreamEvent


# 内置系统提示词
# 告诉模型它在终端环境中运行，作为 AI 编程助手
SYSTEM_PROMPT = """\
You are gurkecode, an AI assistant running in the terminal.

You are designed to help users with software engineering tasks — reading, writing, \
and reasoning about code. You have access to the current working directory and can \
discuss files, architecture, and implementation details.

Be concise and direct. When discussing code, reference file paths and line numbers. \
Use markdown for code blocks, lists, and structured responses.

The user is a developer working at the command line. Adapt your responses accordingly.\
"""


class ChatEngine:
    """对话引擎。

    维护单次会话内的完整对话历史，编排每次 LLM 请求的上下文组装、
    流式事件分发和响应收尾工作。

    使用方式：
        engine = ChatEngine()
        engine.add_user_message("你好")
        async for event in engine.stream_response(protocol, config):
            if event.kind == StreamEvent.KIND_TEXT_DELTA:
                ui.append(event.text)
            elif event.kind == StreamEvent.KIND_DONE:
                ui.finalize()
            elif event.kind == StreamEvent.KIND_ERROR:
                ui.show_error(event.error)
    """

    def __init__(self):
        """初始化对话引擎。

        创建空的对话历史列表。
        系统提示词在每次请求时作为消息列表的第一条加入，不持久化在历史中。
        """
        self._history: list[ChatMessage] = []

    @property
    def history(self) -> list[ChatMessage]:
        """返回当前对话历史的只读视图。

        注意：返回的是内部列表的引用，外部不应直接修改。
        历史中只包含 user 和 assistant 消息，不含 system 消息。
        """
        return self._history

    def add_user_message(self, text: str) -> None:
        """将用户消息追加到对话历史。

        Args:
            text: 用户输入的文本内容
        """
        self._history.append(ChatMessage(role="user", content=text))

    async def stream_response(
        self,
        protocol: ChatProtocol,
        config: ProviderConfig,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求并逐项产出事件。

        执行流程：
        1. 组装完整上下文：system prompt + 历史消息
        2. 调用 protocol.chat() 获取 SSE 事件流
        3. 过滤 thinking_delta 事件（不产出给调用方）
        4. 累积 text_delta 文本
        5. 收到 done 事件后将完整回复追加到历史
        6. 收到 error 事件后直接产出（不追加到历史）

        Args:
            protocol: 协议适配器实例
            config: 服务商配置（模型名、thinking 开关等）

        Yields:
            StreamEvent 序列，依次为 text_delta... → done/error
        """
        # ---- 1. 组装上下文 ----
        # 以 system prompt 开头，后跟完整对话历史 
        full_messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT)
        ] + self._history

        # ---- 2. 调用协议层流式接口 ----
        accumulated_text = ""  # 累积本轮 assistant 的全部回复文本

        async for event in protocol.chat(
            messages=full_messages,
            model=config.model,
            thinking=config.thinking,
        ):
            if event.kind == StreamEvent.KIND_TEXT_DELTA:
                # 正文增量：累积并产出给调用方
                accumulated_text += event.text
                yield event

            elif event.kind == StreamEvent.KIND_THINKING_DELTA:
                # （这里什么也不做，继续等待下一个事件）
                pass

            elif event.kind == StreamEvent.KIND_DONE:
                # 回复正常结束：
                # - 将完整回复文本追加到对话历史
                # - 产出 done 事件通知调用方
                if accumulated_text.strip():
                    self._history.append(
                        ChatMessage(role="assistant", content=accumulated_text)
                    )
                yield event
                return

            elif event.kind == StreamEvent.KIND_ERROR:
                # 错误：直接产出，不修改历史
                yield event
                return

        # 如果循环正常结束但没收到 done/error（边界情况）
        if accumulated_text.strip():
            self._history.append(
                ChatMessage(role="assistant", content=accumulated_text)
            )
        yield StreamEvent(kind=StreamEvent.KIND_DONE)

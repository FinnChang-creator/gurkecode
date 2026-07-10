"""对话引擎

维护多轮对话的上下文历史，编排每次请求，分发流式事件，管理响应计时。
在工具模式下，编排"检测工具调用 → 执行 → 回灌 → 再请求"的完整循环。

ChatEngine 是 UI 和 Protocol 之间的桥梁：
- UI 调用 add_user_message() 记录用户输入
- UI 遍历 stream_response() 获取流式事件并驱动界面更新
- ChatEngine 负责组装 system prompt + history、调用 protocol.chat()、
  过滤 thinking 内容、解析工具调用、执行工具、回灌结果、
  记录 assistant 回复到历史
"""

from collections.abc import AsyncIterator

from config.models import ProviderConfig
from protocol.models import ChatMessage, ChatProtocol, StreamEvent
from tools.base import ToolResult
from tools.registry import ToolRegistry


# 内置系统提示词
# 告诉模型它在终端环境中运行，作为 AI 编程助手
# 当有工具可用时，追加工具使用约定说明
SYSTEM_PROMPT = """\
You are gurkecode, an AI assistant running in the terminal.

You are designed to help users with software engineering tasks — reading, writing, \
and reasoning about code. You have access to the current working directory and can \
discuss files, architecture, and implementation details.

Be concise and direct. When discussing code, reference file paths and line numbers. \
Use markdown for code blocks, lists, and structured responses.

The user is a developer working at the command line. Adapt your responses accordingly.\
"""

# 工具系统追加提示：当有工具可用时追加到 system prompt
TOOLS_APPEND_PROMPT = """

You have access to tools that let you read files, write files, edit files, \
execute shell commands, search for files by pattern, and search file contents. \
When you need information that a tool can provide, use it — don't guess. \
When the user asks you to perform an action (read a file, run a command, etc.), \
use the appropriate tool immediately.

After receiving tool results, use them to give a complete, accurate answer. \
You may call tools across multiple rounds until the task is complete — \
each round you can request new tools based on previous results. \
When you have enough information, provide your final text answer \
without requesting additional tools.\
"""

# Agent Loop 最大循环轮次上限
# 防止模型陷入无限工具调用循环，达到上限后强制终止并以不带工具的请求收尾
MAX_AGENT_ITERATIONS = 50


class ChatEngine:
    """对话引擎。

    维护单次会话内的完整对话历史，编排每次 LLM 请求的上下文组装、
    流式事件分发和 Agent Loop 循环。

    Agent Loop 流程：
    while 轮次 < 上限：
      发起请求（带工具）→ 检测工具调用 → 执行 → 回灌历史 → 继续下一轮
      模型返回纯文本（无工具调用）时自然终止
    达到上限时强制终止并以不带工具的请求收尾
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
        在工具模式下，历史中还可能包含 tool 角色消息。
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
        tool_registry: ToolRegistry | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求并逐项产出事件。

        在有工具注册中心时：
        1. 进入 Agent Loop，每轮请求都携带工具定义
        2. 收集工具调用、产出工具事件
        3. 如有工具调用：执行、回灌，进入下一轮循环
        4. 模型返回纯文本答复时自然终止
        5. 达到 MAX_AGENT_ITERATIONS 上限时强制终止并收尾

        无工具注册中心时：单轮请求，行为与 ch02 完全一致。

        Args:
            protocol: 协议适配器实例
            config: 服务商配置（模型名、thinking 开关等）
            tool_registry: 可选工具注册中心，None 表示不使用工具

        Yields:
            StreamEvent 序列：text_delta / tool_call_* / tool_executed / done / error
        """
        # ---- 1. 组装上下文 ----
        system_content = SYSTEM_PROMPT
        if tool_registry is not None and len(tool_registry.list_tools()) > 0:
            system_content += TOOLS_APPEND_PROMPT

        full_messages = [
            ChatMessage(role="system", content=system_content)
        ] + self._history

        # ---- 导出工具定义 ----
        tool_defs: list[dict] | None = None
        if tool_registry is not None:
            tool_defs = tool_registry.export_definitions()

        # ---- 2. 发起流式请求 ----
        async for event in self._do_agent_loop(
            protocol, config, tool_registry, full_messages,
            tool_defs, system_content,
        ):
            yield event

    async def _do_agent_loop(
        self,
        protocol: ChatProtocol,
        config: ProviderConfig,
        tool_registry: ToolRegistry | None,
        messages: list[ChatMessage],
        tool_defs: list[dict] | None,
        system_content: str,
    ) -> AsyncIterator[StreamEvent]:
        """Agent Loop 主循环。

        循环发起请求（每轮都带工具定义），检测工具调用，
        执行、回灌，直到模型给出纯文本答复或达到轮次上限。

        与 ch03 的关键区别：
        - ch03 硬编码两轮（第一轮带工具 → 第二轮不带工具）
        - 现在 while 循环，每轮都带工具，模型可多次请求不同工具
        - 达到 MAX_AGENT_ITERATIONS 后强制终止并以不带工具的请求收尾
        """
        from protocol.models import ToolCall as TCModel

        # 当前轮次使用的消息列表，每轮执行工具后会更新
        current_messages = messages
        iteration = 0

        while iteration < MAX_AGENT_ITERATIONS:
            accumulated_text = ""
            pending_tool_calls: list[dict] = []
            has_executed_tools = False  # 本轮是否执行了工具（决定 break 后行为）

            # ---- 发起本轮请求（始终携带工具定义） ----
            async for event in protocol.chat(
                messages=current_messages,
                model=config.model,
                thinking=config.thinking,
                tools=tool_defs,
            ):
                if event.kind == StreamEvent.KIND_TEXT_DELTA:
                    accumulated_text += event.text
                    yield event

                elif event.kind == StreamEvent.KIND_THINKING_DELTA:
                    pass

                elif event.kind == StreamEvent.KIND_TOOL_CALL_START:
                    pending_tool_calls.append({
                        "id": event.tool_call_id,
                        "name": event.tool_call_name,
                        "args_str": "",
                        "started": True,
                        "ended": False,
                    })
                    yield event

                elif event.kind == StreamEvent.KIND_TOOL_CALL_DELTA:
                    # 按 tool_call_id 精确匹配目标工具调用，不能简单取 [-1]
                    # 多工具并行时 delta 事件可能不按追加顺序到达
                    for tc in pending_tool_calls:
                        if tc["id"] == event.tool_call_id and not tc["ended"]:
                            tc["args_str"] += event.text
                            break
                    yield event

                elif event.kind == StreamEvent.KIND_TOOL_CALL_END:
                    for tc in pending_tool_calls:
                        if tc["id"] == event.tool_call_id and not tc["ended"]:
                            tc["ended"] = True
                            tc["arguments"] = event.tool_arguments or {}
                            break
                    yield event

                elif event.kind == StreamEvent.KIND_DONE:
                    # 检查是否有已完成的工具调用
                    completed_tools = [
                        tc for tc in pending_tool_calls
                        if tc.get("ended") and "arguments" in tc
                    ]

                    if completed_tools and tool_registry:
                        # ---- 有工具调用：构建 assistant 消息 ----
                        tool_calls_list = [
                            TCModel(
                                id=tc["id"],
                                name=tc["name"],
                                arguments=tc["arguments"],
                            )
                            for tc in completed_tools
                        ]

                        self._history.append(
                            ChatMessage(
                                role="assistant",
                                content=accumulated_text.strip(),
                                tool_calls=tool_calls_list if tool_calls_list else None,
                            )
                        )

                        # ---- 执行工具 ----
                        for tc in completed_tools:
                            tool_name = tc["name"]
                            call_id = tc["id"]
                            args = tc["arguments"]

                            tool = tool_registry.get(tool_name)
                            if tool is None:
                                result = ToolResult.fail(
                                    call_id, tool_name,
                                    f"未知工具：{tool_name}，可用工具有："
                                    + ", ".join(
                                        t.name for t in tool_registry.list_tools()
                                    ),
                                )
                            else:
                                args["call_id"] = call_id
                                try:
                                    result = await tool.execute(args)
                                except Exception as e:
                                    result = ToolResult.fail(
                                        call_id, tool_name,
                                        f"工具执行异常：{e}",
                                    )

                            # 产出工具结果事件
                            yield StreamEvent(
                                kind=StreamEvent.KIND_TOOL_EXECUTED,
                                tool_call_id=call_id,
                                tool_call_name=tool_name,
                                text=result.content,
                                tool_arguments={"success": result.success},
                            )

                            # 回灌工具结果到历史
                            self._history.append(
                                ChatMessage(
                                    role="tool",
                                    content=result.content,
                                    tool_call_id=call_id,
                                    name=tool_name,
                                )
                            )

                        # ---- 准备下一轮消息 ----
                        # 将 system prompt + 更新后的 history 作为下一轮的消息
                        current_messages = [
                            ChatMessage(role="system", content=system_content)
                        ] + self._history
                        # 退出内层 async for，外层 while 继续下一轮
                        has_executed_tools = True
                        break

                    # ---- 无工具调用：模型给出纯文本答复，正常结束 ----
                    if accumulated_text.strip():
                        self._history.append(
                            ChatMessage(role="assistant", content=accumulated_text)
                        )
                    yield event
                    return

                elif event.kind == StreamEvent.KIND_ERROR:
                    yield event
                    return

            # ---- 内层 async for 之后 ----
            # break（有工具已执行）→ iteration += 1，进入下一轮
            # 流异常结束（无 break/return 到达此处）→ 收尾退出
            if has_executed_tools:
                # 本轮有工具已执行，准备下一轮循环
                iteration += 1
                continue

            # 边界情况：流正常结束但未收到 done/error
            if accumulated_text.strip():
                self._history.append(
                    ChatMessage(role="assistant", content=accumulated_text)
                )
            yield StreamEvent(kind=StreamEvent.KIND_DONE)
            return

        # ---- 达到最大轮次上限 ----
        limit_msg = (
            f"已达到最大工具调用轮次（{MAX_AGENT_ITERATIONS} 轮），"
            "请基于已有的工具调用结果给出当前最佳答复。"
        )
        self._history.append(ChatMessage(role="user", content=limit_msg))

        # 以不带工具的最后一次请求收尾，产出纯文本答复
        final_messages = [
            ChatMessage(role="system", content=system_content)
        ] + self._history

        final_text = ""
        async for event in protocol.chat(
            messages=final_messages,
            model=config.model,
            thinking=config.thinking,
            tools=None,
        ):
            if event.kind == StreamEvent.KIND_TEXT_DELTA:
                final_text += event.text
                yield event
            elif event.kind == StreamEvent.KIND_THINKING_DELTA:
                pass
            elif event.kind == StreamEvent.KIND_DONE:
                if final_text.strip():
                    self._history.append(
                        ChatMessage(role="assistant", content=final_text)
                    )
                yield event
                return
            elif event.kind == StreamEvent.KIND_ERROR:
                yield event
                return

        # 上限最终请求边界情况
        if final_text.strip():
            self._history.append(
                ChatMessage(role="assistant", content=final_text)
            )
        yield StreamEvent(kind=StreamEvent.KIND_DONE)

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
from prompt.builder import PromptBuilder, create_default_sections
from prompt.environment import collect_environment, format_environment
from prompt.reminder import ReminderBuilder, ReminderState
from tools.base import ToolResult
from tools.registry import ToolRegistry


# Agent Loop 最大循环轮次上限
# 防止模型陷入无限工具调用循环，达到上限后强制终止并以不带工具的请求收尾
MAX_AGENT_ITERATIONS = 50

# /do 触发消息
# 当用户输入 /do 时，通过 reminder 机制注入此消息，
# 触发模型按前面制定的计划执行
DO_TRIGGER_MESSAGE = (
    "请按照上面制定的计划，现在开始执行。"
    "你可以使用所有工具（包括写入文件、编辑文件和执行命令）。"
    "按照计划中的步骤和顺序逐一实施，每完成一个步骤后评估结果再继续下一步。"
)


class ChatEngine:
    """对话引擎。

    维护单次会话内的完整对话历史，编排每次 LLM 请求的上下文组装、
    流式事件分发和 Agent Loop 循环。

    Agent Loop 流程：
    while 轮次 < 上限：
      发起请求（带工具）→ 检测工具调用 → 执行 → 回灌历史 → 继续下一轮
      模型返回纯文本（无工具调用）时自然终止
    达到上限时强制终止并以不带工具的请求收尾

    计划模式（Plan Mode）：
    - /plan 进入：仅注入只读工具，使用计划态系统提示，
      模型只能探索和制定计划，不能动手改动。
    - /do 退出：恢复全工具，注入触发消息让模型按计划执行。
    - 模式跨轮保持，直到再次切换。
    """

    def __init__(self):
        """初始化对话引擎。

        创建空的对话历史列表，初始为非计划模式，
        初始化模块化系统提示拼装器和提醒状态。
        系统提示词在每次请求时动态拼装，不持久化在历史中。
        """
        self._history: list[ChatMessage] = []
        self._plan_mode: bool = False

        # 模块化系统提示拼装器（跨轮复用，内容稳定）
        self._prompt_builder: PromptBuilder = create_default_sections()

        # 补充消息注入状态（追踪计划模式提醒轮次）
        self._reminder_state = ReminderState()
        self._reminder_builder = ReminderBuilder(self._reminder_state)

    @property
    def history(self) -> list[ChatMessage]:
        """返回当前对话历史的只读视图。

        注意：返回的是内部列表的引用，外部不应直接修改。
        历史中只包含 user 和 assistant 消息，不含 system 消息。
        在工具模式下，历史中还可能包含 tool 角色消息。
        """
        return self._history

    @property
    def plan_mode(self) -> bool:
        """当前是否处于计划模式。

        计划模式下仅注入只读工具定义 + 计划态系统提示，
        模型只能探索和分析，不能修改文件或执行命令。

        Returns:
            True 表示当前处于计划模式
        """
        return self._plan_mode

    def enter_plan_mode(self) -> None:
        """进入计划模式。

        此后所有请求将：
        - 仅注入只读工具定义（read_file / glob_search / grep_search）
        - 通过 reminder 机制注入计划态约束（不替换系统提示，保持缓存命中）
        模式跨轮保持，直到调用 exit_plan_mode()。
        """
        self._plan_mode = True
        # 重置提醒轮次：新计划模式从第 0 轮开始
        self._reminder_state.iteration = 0

    def exit_plan_mode(self) -> None:
        """退出计划模式。

        恢复为全工具定义和标准系统提示。
        通常配合 /do 使用：退出后立即注入触发消息让模型按计划执行。
        """
        self._plan_mode = False

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
        # ---- 1. 组装系统提示 ----
        # F1/F3: 模块化拼装稳定系统提示（跨轮不变，可缓存）
        stable_system = self._prompt_builder.build()

        # F2: 采集环境信息（每轮可能变化，不走缓存）
        env_info = await collect_environment(config.model)
        env_text = format_environment(env_info)

        # 两条独立的 system 消息：
        # 第一条：稳定模块 → Anthropic 打 cache_control，OpenAI 放最前前缀缓存
        # 第二条：环境信息 → 每次都变化，不缓存
        system_msgs = [
            ChatMessage(role="system", content=stable_system),
            ChatMessage(role="system", content=env_text),
        ]

        full_messages = system_msgs + self._history

        # ---- 导出工具定义 ----
        # 计划模式下仅注入只读工具，正常模式下注入全部工具
        # 系统提示保持不变（不替换文本），计划态约束由 reminder 注入
        tool_defs: list[dict] | None = None
        if tool_registry is not None:
            if self._plan_mode:
                tool_defs = tool_registry.export_read_only_definitions()
                # 如果没有注册任何只读工具，tool_defs 保持为 None
                if not tool_defs:
                    tool_defs = None
            else:
                tool_defs = tool_registry.export_definitions()

        # ---- 2. 发起流式请求 ----
        async for event in self._do_agent_loop(
            protocol, config, tool_registry, full_messages,
            tool_defs, system_msgs,
        ):
            yield event

    async def _do_agent_loop(
        self,
        protocol: ChatProtocol,
        config: ProviderConfig,
        tool_registry: ToolRegistry | None,
        messages: list[ChatMessage],
        tool_defs: list[dict] | None,
        system_msgs: list[ChatMessage],
    ) -> AsyncIterator[StreamEvent]:
        """Agent Loop 主循环。

        循环发起请求（每轮都带工具定义），检测工具调用，
        执行、回灌，直到模型给出纯文本答复或达到轮次上限。

        与 ch04 的关键区别：
        - ch04 硬编码两轮（第一轮带工具 → 第二轮不带工具）
        - 现在 while 循环，每轮都带工具，模型可多次请求不同工具
        - 达到 MAX_AGENT_ITERATIONS 后强制终止并以不带工具的请求收尾
        - 计划模式下每轮注入 system-reminder（频率可控）
        """
        from protocol.models import ToolCall as TCModel

        # 当前轮次使用的消息列表，每轮执行工具后会更新
        current_messages = messages
        iteration = 0

        while iteration < MAX_AGENT_ITERATIONS:
            # ---- 补充消息注入（F6/F7） ----
            # 计划模式下每轮注入 system-reminder，不写入持久历史
            reminder_msg = None
            if self._plan_mode:
                reminder_msg = self._reminder_builder.build_plan_reminder()
                self._reminder_state.iteration += 1

            # 本轮消息 = system 消息 + 可选 reminder + 历史
            round_messages = list(current_messages)
            if reminder_msg:
                round_messages.append(reminder_msg)

            accumulated_text = ""
            pending_tool_calls: list[dict] = []
            has_executed_tools = False  # 本轮是否执行了工具（决定 break 后行为）

            # ---- 发起本轮请求（始终携带工具定义） ----
            async for event in protocol.chat(
                messages=round_messages,
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
                        # 将 system 消息 + 更新后的 history 作为下一轮的消息
                        current_messages = system_msgs + self._history
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
        final_messages = system_msgs + self._history

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

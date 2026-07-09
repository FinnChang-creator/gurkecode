"""主应用 GurkeApp

基于 textual.App 的全功能终端对话界面。
组合 Banner、ChatView、InputBox、StatusBar 四个 widget，
协调消息发送、流式响应更新、工具调用展示和计时器。

多 provider 时，启动后先展示 ProviderSelect 选择界面；
选定后再进入对话界面。
"""

import asyncio

from textual.app import App, ComposeResult
from textual.binding import Binding

from config.models import ProviderConfig
from engine import ChatEngine
from protocol.adapter import create_protocol
from protocol.models import ChatProtocol, StreamEvent
from tools import registry as tool_registry
from ui.banner import Banner
from ui.chat_view import ChatView
from ui.input_box import InputBox
from ui.provider_select import ProviderSelect
from ui.status_bar import StatusBar


class GurkeApp(App):
    """gurkecode 主应用。

    基于 textual 框架的全功能终端对话客户端。
    接收所有可用服务商列表，若多于一个则先弹出选择界面。

    Attributes:
        _providers: 所有可用服务商配置列表
        _config: 当前选定（或唯一）的服务商配置
        _engine: 对话引擎（历史管理 + 请求编排）
        _protocol: 协议适配器（发起 HTTP 流式请求）
        _timer_task: 计时器异步任务
        _stream_task: 当前流式请求任务（可被 Escape 键取消）
        _is_streaming: 是否正在等待/接收流式响应
    """

    # 应用的 CSS 样式
    # 布局：Banner（顶部固定）→ ChatView（占满剩余空间）→ InputBox+StatusBar（底部固定）
    CSS = """
    Screen {
        layout: vertical;
    }
    Banner {
        dock: top;
    }
    ChatView {
        height: 1fr;
    }
    InputBox {
        dock: bottom;
    }
    StatusBar {
        dock: bottom;
        height: 1;
    }
    """

    # 全局键盘绑定
    BINDINGS = [
        Binding("ctrl+c", "quit", "退出", show=False),
        Binding("escape", "cancel_stream", "中止流式", show=False),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
    ) -> None:
        """初始化主应用。

        Args:
            providers: 所有可用服务商配置列表（至少 1 个，已校验）
        """
        super().__init__()
        self._providers = providers
        self._config: ProviderConfig | None = None
        self._engine: ChatEngine | None = None
        self._protocol: ChatProtocol | None = None
        self._timer_task: asyncio.Task | None = None
        self._stream_task: asyncio.Task | None = None  # 当前流式请求任务（可被取消）
        self._is_streaming = False

    def compose(self) -> ComposeResult:
        """组合 UI 各组件。

        布局（从上到下）：
        Banner → ChatView（1fr 填满空间）→ InputBox → StatusBar
        """
        yield Banner()
        yield ChatView()
        yield InputBox()
        yield StatusBar()

    def on_mount(self) -> None:
        """应用挂载后初始化。

        若仅一个 provider 则直接使用；
        若多个则通过 worker 弹出 ProviderSelect 选择界面。
        """
        if len(self._providers) == 1:
            # 单 provider：直接进入对话
            self.run_worker(self._start_chat(self._providers[0]))
        else:
            # 多 provider：通过 worker 弹出选择界面
            self.run_worker(self._show_provider_select())

    async def _start_chat(self, config: ProviderConfig) -> None:
        """选定 provider 后初始化对话组件。

        Args:
            config: 选定的服务商配置
        """
        self._config = config
        self._protocol = create_protocol(config)
        self._engine = ChatEngine()

        # 更新状态栏
        status_bar = self.query_one(StatusBar)
        status_bar.set_provider(config.name, config.model)

        # 显示就绪提示
        chat_view = self.query_one(ChatView)
        chat_view.append_system(
            f"就绪 — 使用 {config.name} ({config.model}) 开始对话。"
            " 输入 /exit 退出。"
        )

        # 将焦点聚焦到输入框
        self.query_one(InputBox).focus()

    async def _show_provider_select(self) -> None:
        """弹出 provider 选择界面，用户选择后进入对话。

        使用 push_screen_wait 等待用户选择，
        选定后调用 _start_chat 初始对话组件。
        """
        selected = await self.push_screen_wait(
            ProviderSelect(self._providers)
        )
        if selected is None:
            selected = self._providers[0]
        await self._start_chat(selected)

    async def _handle_submit(self, text: str) -> None:
        """处理用户提交的文本。

        完整的对话流程：
        1. 检查 /exit 命令 → 退出
        2. 追加用户消息 → 禁止提交（但保持可键入）→ 启动计时
        3. 遍历流式事件 → 更新 ChatView
        4. 用户可按 Escape 取消流式（触发 asyncio.CancelledError）
        5. 收尾：markdown 渲染 / 错误展示 → 停止计时 → 恢复提交

        Args:
            text: 用户输入的文本
        """
        # ---- 1. /exit 命令：安全退出 ----
        if text.strip() == "/exit":
            self.exit()
            return

        # ---- 2. 准备工作 ----
        chat_view = self.query_one(ChatView)
        status_bar = self.query_one(StatusBar)
        input_box = self.query_one(InputBox)

        # 追加用户消息到对话区和引擎历史
        chat_view.append_user(text)
        self._engine.add_user_message(text)

        # 禁止提交（但保持输入框可编辑，用户可提前键入下一条消息）
        input_box.disabled = True
        self._is_streaming = True

        # 启动响应计时器
        self._timer_task = asyncio.create_task(self._run_timer(status_bar))

        try:
            # ---- 3. 流式事件处理 ----
            async for event in self._engine.stream_response(
                self._protocol, self._config, tool_registry
            ):
                if event.kind == StreamEvent.KIND_TEXT_DELTA:
                    chat_view.append_streaming(event.text)

                elif event.kind == StreamEvent.KIND_TOOL_CALL_START:
                    # 工具调用开始：在对话区显示工具行
                    chat_view.append_tool_call(
                        event.tool_call_id, event.tool_call_name
                    )

                elif event.kind == StreamEvent.KIND_TOOL_CALL_END:
                    # 工具调用参数完整：更新工具行显示关键参数
                    if event.tool_arguments:
                        # 提取关键参数作为摘要
                        args_keys = list(event.tool_arguments.keys())
                        if args_keys:
                            # 取第一个参数值作为摘要（如 read_file 的 path）
                            first_val = str(event.tool_arguments.get(args_keys[0], ""))
                            if len(first_val) > 40:
                                first_val = first_val[:40] + "..."
                            chat_view.append_tool_call(
                                event.tool_call_id,
                                event.tool_call_name,
                                first_val,
                            )

                elif event.kind == StreamEvent.KIND_TOOL_EXECUTED:
                    # 工具执行完毕：显示结果摘要
                    success = (
                        event.tool_arguments.get("success", False)
                        if event.tool_arguments
                        else False
                    )
                    # 生成简洁的结果摘要
                    result_text = event.text
                    if len(result_text) > 80:
                        result_text = result_text[:80] + "..."
                    chat_view.update_tool_result(
                        event.tool_call_id, result_text, success
                    )

                elif event.kind == StreamEvent.KIND_DONE:
                    self._cancel_timer()
                    status_bar.set_idle()
                    if self._engine.history:
                        last_msg = self._engine.history[-1]
                        if last_msg.role == "assistant":
                            chat_view.finalize_markdown(last_msg.content)

                elif event.kind == StreamEvent.KIND_ERROR:
                    self._cancel_timer()
                    status_bar.set_idle()
                    chat_view.append_error(event.error)

        except asyncio.CancelledError:
            # 用户按 Escape 手动中止流式响应
            self._cancel_timer()
            status_bar.set_idle()
            # 清理 ChatView 的流式状态，防止残留 widget 污染下一轮对话
            chat_view.cancel_streaming()
            chat_view.append_system("▸ 已中止 — 流式响应被用户取消")

        except Exception as e:
            self._cancel_timer()
            status_bar.set_idle()
            chat_view.append_error(f"内部错误：{e}")

        finally:
            self._cancel_timer()
            input_box.disabled = False
            self._is_streaming = False
            self._stream_task = None
            input_box.focus()

    async def _run_timer(self, status_bar: StatusBar) -> None:
        """计时器协程。

        每秒更新一次 StatusBar 的计时显示，
        直到被取消（流式结束或出错）。

        Args:
            status_bar: 状态栏实例
        """
        elapsed = 0.0
        try:
            while True:
                status_bar.set_timer(elapsed)
                await asyncio.sleep(1.0)
                elapsed += 1.0
        except asyncio.CancelledError:
            status_bar.set_timer_stopped(elapsed)

    def _cancel_timer(self) -> None:
        """取消计时器任务。"""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            self._timer_task = None

    # ---- 事件处理 ----

    def on_input_box_submitted(self, event: InputBox.Submitted) -> None:
        """监听 InputBox 的提交事件。

        Args:
            event: InputBox.Submitted 消息
        """
        if self._is_streaming:
            return
        # 保存任务引用，以便 action_cancel_stream 可以取消它
        self._stream_task = asyncio.create_task(self._handle_submit(event.text))

    def action_cancel_stream(self) -> None:
        """Escape 键处理：中止当前正在进行的流式请求。"""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            self._stream_task = None

    def action_quit(self) -> None:
        """Ctrl+C 处理：安全退出应用。"""
        self._cancel_timer()
        self.exit()

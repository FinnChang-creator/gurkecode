"""对话区 Widget

显示用户和助手的对话历史，支持：
- 纯文本流式追加（流式期间逐字显示）
- Markdown 定型渲染（回复结束后美化）
- 可区分的错误样式
- 终端宽度自适应
"""

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from ui.models import DisplayMessage, MessageRole, MessageState


class ChatView(VerticalScroll):
    """对话区域。

    以时间顺序展示所有用户输入、助手回复和错误信息。
    基于 textual 的 VerticalScroll 容器，自动跟随最新内容。

    每条消息是一个子 widget：
    - 用户消息：Static，右侧对齐样式
    - 助手消息（流式）：Static，纯文本，动态更新
    - 助手消息（完成）：Markdown，渲染美化
    - 错误消息：Static，红色/警告样式

    使用方式：
        chat_view = ChatView()
        chat_view.append_user("你好")
        chat_view.append_streaming("你好！我")  # 多次调用
        chat_view.append_streaming("是 gurkecode")
        chat_view.finalize_markdown("你好！我是 gurkecode")  # 流式结束
        chat_view.append_error("请求超时")
    """

    DEFAULT_CSS = """
    ChatView {
        padding: 1;
    }
    ChatView .user-message {
        color: $text-muted;
        padding: 1 0;
    }
    ChatView .assistant-message {
        padding: 1 0;
    }
    ChatView .error-message {
        color: $error;
        padding: 1 0;
        text-style: italic;
    }
    ChatView .system-message {
        color: $text-muted;
        padding: 1 0;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._streaming_widget: Static | None = None  # 当前正在流式更新的 widget
        self._streaming_text: str = ""  # 累积的流式文本

    def append_user(self, text: str) -> None:
        """追加一条用户消息到对话区。

        Args:
            text: 用户输入的文本
        """
        user_widget = Static(f"[dim]❯ {text}[/dim]", classes="user-message")
        self.mount(user_widget)
        self._scroll_to_bottom()

    def append_system(self, text: str) -> None:
        """追加一条系统提示消息到对话区。

        以 dim 样式展示，区别于用户/助手/错误消息。
        用于就绪提示等系统级别信息。

        Args:
            text: 系统提示文本
        """
        system_widget = Static(text, classes="system-message")
        self.mount(system_widget)
        self._scroll_to_bottom()

    def append_streaming(self, text: str) -> None:
        """追加流式文本增量。

        首次调用时创建一个新的 Static widget，
        后续调用更新同一个 widget 的内容，实现逐字显示效果。

        Args:
            text: 文本增量片段
        """
        self._streaming_text += text

        if self._streaming_widget is None:
            # 首次流式增量：创建新 widget
            self._streaming_widget = Static(
                self._streaming_text, classes="assistant-message"
            )
            self.mount(self._streaming_widget)
        else:
            # 更新已有 widget 的内容
            self._streaming_widget.update(self._streaming_text)

        self._scroll_to_bottom()

    def finalize_markdown(self, full_text: str) -> None:
        """流式结束后，将纯文本替换为 Markdown 渲染版本。

        移除流式期间的 Static widget，挂载 textual 的 Markdown widget。
        如果 Markdown 渲染失败（如内容为空），保留纯文本显示。

        Args:
            full_text: 本轮助手的完整回复文本
        """
        # 移除流式 widget
        if self._streaming_widget is not None:
            self._streaming_widget.remove()
            self._streaming_widget = None

        self._streaming_text = ""

        if not full_text.strip():
            return

        # 用 textual 内置的 Markdown widget 进行美化渲染
        md_widget = Markdown(full_text, classes="assistant-message")
        self.mount(md_widget)
        self._scroll_to_bottom()

    def cancel_streaming(self) -> None:
        """取消当前流式输出。

        移除正在流式更新的 widget，重置流式状态。
        流式被用户中止时调用，防止残留的 _streaming_widget
        污染下一轮对话的流式显示。
        """
        if self._streaming_widget is not None:
            self._streaming_widget.remove()
            self._streaming_widget = None
        self._streaming_text = ""

    def append_error(self, text: str) -> None:
        """追加一条错误消息。

        以可区分的错误样式（红色、斜体）显示。

        Args:
            text: 错误描述文本
        """
        error_widget = Static(f"✗ {text}", classes="error-message")
        self.mount(error_widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """滚动到对话区底部，跟随最新内容。"""
        self.scroll_end(animate=False)

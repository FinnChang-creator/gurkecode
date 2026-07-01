"""输入框 Widget

底部带边框的文本输入框，❯ 提示符。
支持 Enter 提交、Alt+Enter/Shift+Enter 换行。
流式响应期间输入框保持可编辑（可提前键入），仅拦截 Enter 提交。

设计思路：
- 基于 textual 的 TextArea 实现多行编辑能力
- 通过 _on_key 拦截 Enter 键，将其从"换行"转为"提交"
- Alt+Enter / Shift+Enter 显式插入换行
- disabled 状态仅阻塞提交，不阻塞键入（read_only 不启用）
"""

from textual.containers import Container
from textual.message import Message
from textual.widgets import Static, TextArea


class SubmitTextArea(TextArea):
    """定制的 TextArea 子类。

    重载 Enter 键行为：Enter 提交文本，而非默认的插入换行。
    Alt+Enter / Shift+Enter 插入换行。

    通过 _on_key 在 key 处理的最早阶段拦截 Enter，
    比 BINDINGS 优先级更高，避免与 TextArea 内置绑定冲突。

    Attributes:
        submit_blocked: 由外部（InputBox）控制。为 True 时 Enter 键被吞掉，
                       既不提交也不清空——文本保留在输入框中。
    """

    class SubmitRequested(Message):
        """文本提交请求（从 TextArea 向 InputBox 冒泡）。

        Attributes:
            text: 要提交的文本内容
        """

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submit_blocked: bool = False

    def _on_key(self, event) -> None:
        """拦截键盘事件。

        - Enter（无修饰键）且 submit_blocked=False：提交文本（清空 + 发消息）
        - Enter（无修饰键）且 submit_blocked=True：吞掉按键，文本保留不动
        - Alt+Enter / Shift+Enter：在光标位置插入换行，支持多行编辑
        - 其他键：放行，TextArea 默认处理

        注意：不能用"放行 alt+enter"依赖 TextArea 默认行为，因为 TextArea
        自带的 enter→换行绑定只匹配纯 "enter"，不匹配 "alt+enter" / "shift+enter"，
        必须显式调用 insert("\n")。

        Args:
            event: textual 的 Key 事件对象
        """
        if event.key == "enter":
            # 纯 Enter：阻止默认换行行为
            event.stop()
            event.prevent_default()
            if self.submit_blocked:
                # 流式期间：吞掉 Enter，不清空文本也不提交
                return
            # 正常情况：提交文本
            self._do_submit()
            return

        if event.key in ("alt+enter", "shift+enter"):
            # Alt+Enter / Shift+Enter：阻止默认行为，显式插入换行
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return

        # 其他键：放行，TextArea 默认处理

    def _do_submit(self) -> None:
        """收集当前全部文本，发送 SubmitRequested 消息，然后清空。"""
        text = self.text.strip()
        if text:
            self.post_message(self.SubmitRequested(text))
            self.clear()


class InputBox(Container):
    """用户输入区域。

    组合：带边框容器 + 左侧 ❯ 提示符 + 右侧多行文本编辑区。

    行为：
    - Enter 键：提交当前文本，触发 Submitted 消息，清空输入区
    - Alt+Enter / Shift+Enter：在光标位置插入换行符
    - 流式响应期间（disabled=True）：提交被拦截，但键入不受影响

    暴露的消息：
        Submitted: 用户提交文本时冒泡到父级（GurkeApp）
    """

    DEFAULT_CSS = """
    InputBox {
        height: auto;
        min-height: 3;
        border: solid $primary;
        padding: 0 1;
    }
    InputBox SubmitTextArea {
        height: auto;
        min-height: 1;
        max-height: 8;
    }
    InputBox #prompt {
        width: 2;
        padding: 0 1 0 0;
        color: grey;
    }
    InputBox.disabled {
        border: solid grey;
    }
    """

    class Submitted(Message):
        """用户提交了输入文本的消息（冒泡到 GurkeApp）。

        Attributes:
            text: 用户输入的文本内容
        """

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._disabled = False

    def compose(self) -> None:
        """组合子 widget：❯ 提示符 + SubmitTextArea。"""
        yield Static("❯", id="prompt")
        yield SubmitTextArea.code_editor(
            "",
            language=None,
            id="input-area",
        )

    @property
    def disabled(self) -> bool:
        """是否处于禁用状态（流式响应期间）。"""
        return self._disabled

    @disabled.setter
    def disabled(self, value: bool) -> None:
        """设置流式状态。

        流式期间（disabled=True）：
        - 输入框保持可编辑，用户可提前键入下一条消息
        - Enter 键被吞掉：不提交、不清空、文本保留在输入框中
        - 边框变灰作为视觉提示（通过 CSS class）

        Args:
            value: True 时仅禁止提交，不禁止键入
        """
        self._disabled = value
        # 同步到 SubmitTextArea——在 _on_key 层就拦截 Enter，避免文本被 clear()
        textarea = self.query_one(SubmitTextArea)
        textarea.submit_blocked = value
        if value:
            self.add_class("disabled")
        else:
            self.remove_class("disabled")

    def on_submit_text_area_submit_requested(
        self, event: SubmitTextArea.SubmitRequested
    ) -> None:
        """接收 SubmitTextArea 的提交请求 → 冒泡为 InputBox.Submitted。

        如果处于禁用状态则吞掉事件。

        Args:
            event: SubmitTextArea 的提交请求
        """
        if self._disabled:
            event.stop()
            return

        event.stop()
        self.post_message(self.Submitted(event.text))

"""状态栏 Widget

底部状态栏，显示活动服务商信息、模型名和响应计时。
使用 textual 的 Footer 模式——左侧 provider 名，右侧模型 + 计时。
"""

from textual.widgets import Static


class StatusBar(Static):
    """底部状态栏。

    显示内容分左右两部分：
    - 左侧：活动 service provider 的名称
    - 右侧：模型名 + 计时信息（等待中 / 完成后的总耗时）

    状态栏在应用底部常驻，随对话状态动态更新。
    """

    def __init__(self):
        self._provider_name = ""
        self._model_name = ""
        self._timer_text = ""
        super().__init__("")

    def set_provider(self, name: str, model: str) -> None:
        """设置服务商和模型信息。

        Args:
            name: 服务商可读名称（如 "Claude Sonnet (Anthropic)"）
            model: 模型标识（如 "claude-sonnet-4-6"）
        """
        self._provider_name = name
        self._model_name = model
        self._refresh()

    def set_timer(self, seconds: float) -> None:
        """更新计时显示（流式等待期间每秒调用）。

        Args:
            seconds: 已过去的秒数
        """
        self._timer_text = f"Imagining… ({int(seconds)}s)"
        self._refresh()

    def set_timer_stopped(self, total_seconds: float) -> None:
        """停止计时并显示总耗时（回复结束后调用）。

        Args:
            total_seconds: 本轮请求的总耗时
        """
        self._timer_text = f"({total_seconds:.1f}s)"
        self._refresh()

    def set_idle(self) -> None:
        """重置为空闲状态（无计时显示）。"""
        self._timer_text = ""
        self._refresh()

    def _refresh(self) -> None:
        """根据当前状态更新显示文本。

        格式：[provider_name]                              [model] [timer]
        """
        left = self._provider_name if self._provider_name else "gurkecode"
        right_parts = []
        if self._model_name:
            right_parts.append(self._model_name)
        if self._timer_text:
            right_parts.append(self._timer_text)

        right = " — ".join(right_parts) if right_parts else ""

        # 用空格填充中间区域，模拟左右对齐
        # textual 的 Static 没有原生两端对齐，用固定宽度填充模拟
        if right:
            text = f" {left}{' ' * 8}{right} "
        else:
            text = f" {left} "

        self.update(text)

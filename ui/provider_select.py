"""服务商选择界面

当配置了多个 provider 时，启动后先展示此全屏选择界面。
用户用方向键导航、Enter 确认，选择一个服务商进入对话。
"""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Header, ListItem, ListView, Static

from config.models import ProviderConfig


class ProviderSelect(Screen):
    """服务商选择全屏界面。

    列出所有可用 provider，每项显示名称和模型。
    方向键 ↑↓ 导航，Enter 确认选择并关闭此屏幕。

    使用方式：
        screen = ProviderSelect(providers)
        将 screen 推入 App 的屏幕栈，它会自动处理选择和关闭。
        选择结果通过回调或 App 的方法获取。
    """

    DEFAULT_CSS = """
    ProviderSelect {
        align: center middle;
    }
    ProviderSelect Vertical {
        width: 50;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    ProviderSelect #title {
        text-align: center;
        padding: 1 0;
        text-style: bold;
    }
    ProviderSelect ListView {
        height: auto;
        max-height: 16;
    }
    ProviderSelect #hint {
        text-align: center;
        color: $text-muted;
        padding: 1 0;
    }
    """

    def __init__(self, providers: list[ProviderConfig]) -> None:
        """初始化选择界面。

        Args:
            providers: 可用服务商列表（已校验，至少 2 个）
        """
        super().__init__()
        self._providers = providers
        self._selected_index = 0

    def compose(self) -> ComposeResult:
        """组合选择界面的子 widget。"""
        with Vertical():
            yield Static("选择一个服务商", id="title")

            # 构建列表项：每项显示 "名称 — 模型"
            items = []
            for i, p in enumerate(self._providers):
                label = f" {p.name}  —  [dim]{p.model}[/dim]"
                items.append(ListItem(Static(label), id=f"provider-{i}"))

            yield ListView(*items, initial_index=0)

            yield Static("↑↓ 导航   Enter 确认", id="hint")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """列表项被选中（Enter 确认）时触发。

        从 ListView 自身获取当前高亮项的索引，
        而非依赖解析 ListItem 的 id 属性（更可靠）。

        Args:
            event: ListView 的选择事件
        """
        # 通过查询获取 ListView，读取其 index 属性（当前高亮索引）
        list_view = self.query_one(ListView)
        if list_view.index is not None and 0 <= list_view.index < len(
            self._providers
        ):
            self._selected_index = list_view.index

        # 将选中的 ProviderConfig 返回给调用方（GurkeApp._show_provider_select）
        self.dismiss(self._providers[self._selected_index])

    @property
    def selected_provider(self) -> ProviderConfig:
        """返回当前选中的服务商配置。"""
        return self._providers[self._selected_index]

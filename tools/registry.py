"""工具注册中心

集中管理所有可用工具，提供按名查找和 API 格式导出功能。
上层引擎通过注册中心获取工具列表并注入到 LLM 请求中。
"""

from tools.base import BaseTool


class ToolRegistry:
    """工具注册中心。

    维护一个名字 → 工具实例的映射表，
    支持注册、按名查找、列出全部工具、
    以及导出为协议 API 认可的工具定义列表。

    使用方式：
        registry = ToolRegistry()
        registry.register(ReadFile())
        tool = registry.get("read_file")
        defs = registry.export_definitions()
    """

    def __init__(self):
        """初始化空的注册中心。

        内部以 dict 存储工具映射：{name: BaseTool}。
        """
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例。

        如果同名工具已存在，会覆盖旧实例。
        调用方应确保名称唯一。

        Args:
            tool: 工具实例（BaseTool 子类）
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名称查找工具。

        Args:
            name: 工具名称（如 "read_file"）

        Returns:
            找到的工具实例，未找到返回 None
        """
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """列出所有已注册的工具。

        Returns:
            工具实例列表（无特定顺序）
        """
        return list(self._tools.values())

    def export_definitions(self) -> list[dict]:
        """导出所有工具定义为通用 API 格式。

        返回的是协议无关的中间格式，
        各协议适配器（OpenAI/Anthropic）可在此基础上进一步映射。

        通用格式：
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "...",
                    "parameters": { ... JSON Schema ... }
                }
            }

        Returns:
            工具定义列表，可直接注入到请求体的 "tools" 字段
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def list_read_only_tools(self) -> list[BaseTool]:
        """列出所有只读工具。

        只读工具不会修改文件系统或执行命令，
        用于计划模式（/plan）下限制模型只能探索和分析。

        Returns:
            只读工具实例列表
        """
        return [t for t in self._tools.values() if t.is_read_only]

    def export_read_only_definitions(self) -> list[dict]:
        """只导出只读工具定义为通用 API 格式。

        在计划模式（/plan）下使用，让模型只能看到和使用
        read_file / glob_search / grep_search 等只读工具，
        无法调用 write_file / edit_file / bash 等有副作用的工具。

        格式与 export_definitions() 相同。

        Returns:
            只读工具定义列表
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
            if tool.is_read_only
        ]

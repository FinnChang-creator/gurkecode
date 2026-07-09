"""工具系统基础抽象

定义工具系统的核心数据结构和抽象基类。
所有具体工具实现都继承 BaseTool，产出 ToolResult。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolResult:
    """工具执行结果。

    无论成功或失败都以统一的结构返回，
    调用方不需要捕获异常——错误信息被包在 content 中。

    Attributes:
        call_id: 对应模型发出的工具调用 ID（用于回灌时关联）
        name: 工具名称
        success: 是否执行成功
        content: 成功时的结果文本，或失败时的错误描述
    """

    call_id: str
    name: str
    success: bool
    content: str

    @classmethod
    def ok(cls, call_id: str, name: str, content: str) -> "ToolResult":
        """创建成功结果。"""
        return cls(call_id=call_id, name=name, success=True, content=content)

    @classmethod
    def fail(cls, call_id: str, name: str, error: str) -> "ToolResult":
        """创建失败结果。"""
        return cls(call_id=call_id, name=name, success=False, content=error)


class BaseTool(ABC):
    """工具抽象基类。

    每个工具实现必须提供：
    - name: 工具名称（如 "read_file"），供模型在 tool_calls 中引用
    - description: 给模型看的功能描述，帮助模型判断何时使用
    - parameters: JSON Schema 格式的参数定义，告诉模型如何传参
    - execute(): 执行入口，接收已解析的参数字典，返回 ToolResult

    此外，还可以覆盖以下方法以优化 TUI 显示：
    - display_name: 给用户看的工具名称（中文，如 "读取文件"）
    - format_params(): 将参数格式化为简短摘要
    - format_result(): 将执行结果格式化为简短摘要

    子类只需要实现 execute() 中的具体逻辑，
    错误处理在方法内部完成——execute() 不应该抛出异常。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，如 "read_file"、"bash" 等。

        模型在工具调用中通过此名称引用工具，
        必须与注册中心登记的名称一致。
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具的功能描述，面向模型。

        清晰描述工具做什么、何时使用、注意事项。
        模型根据此描述自主决定是否调用该工具。
        """
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """工具的输入参数定义，JSON Schema 格式。

        例如：
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        """
        ...

    @abstractmethod
    async def execute(self, arguments: dict) -> ToolResult:
        """执行工具，返回结构化结果。

        子类实现应自行捕获所有异常，
        将错误转为 ToolResult.fail() 返回，
        绝不向调用方抛出未处理异常。

        Args:
            arguments: 模型提供的参数（key-value 字典），
                       由协议层从 JSON 参数片段拼接而成

        Returns:
            ToolResult: 执行结果（成功或失败）
        """
        ...

    # ---- 面向 TUI 的显示方法（子类可选覆盖） ----

    @property
    def display_name(self) -> str:
        """返回工具的人类可读名称，用于 TUI 工具行显示。

        子类应覆盖此属性返回中文名称（如 "读取文件" "执行命令"）。
        默认降级为返回内部 name（snake_case 英文名）。

        Returns:
            给用户看的工具名称
        """
        return self.name

    def format_params(self, arguments: dict) -> str:
        """将工具参数格式化为人类可读的简短摘要，显示在工具行上。

        默认返回第一个参数的值（截断至 50 字符）。
        子类可以覆盖此方法来定制显示（例如只显示 path、截断 command 等）。

        Args:
            arguments: 工具的调用参数字典

        Returns:
            简短参数摘要（如文件名、命令等），供 TUI 在 ● 工具名(摘要) 中显示
        """
        if not arguments:
            return ""
        # 跳过内部字段（call_id 是引擎注入的，不应显示）
        display_args = {k: v for k, v in arguments.items() if k != "call_id"}
        if not display_args:
            return ""
        # 取第一个参数值作为摘要
        first_val = str(next(iter(display_args.values()), ""))
        if len(first_val) > 50:
            first_val = first_val[:50] + "..."
        return first_val

    def format_result(self, result: ToolResult) -> str:
        """将工具执行结果格式化为人类可读的简短摘要，显示在结果行上。

        默认截取 content 前 80 字符。子类应覆盖此方法，
        从 ToolResult.content 中提取关键信息（行数、匹配数、退出码等）
        生成简洁的摘要。

        Args:
            result: 工具执行结果

        Returns:
            简短结果摘要（供 TUI 在 ✓/✗ 后显示）
        """
        text = result.content
        if len(text) > 80:
            text = text[:80] + "..."
        return text

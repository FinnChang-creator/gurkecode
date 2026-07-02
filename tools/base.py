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

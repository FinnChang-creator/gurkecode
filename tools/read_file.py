"""读文件工具

读取指定路径的文件内容，带行号输出，方便模型引用。
对文件大小和行数有上限保护，避免撑爆上下文窗口。
"""

import os

from tools.base import BaseTool, ToolResult


# 读取行数上限：超过则不读取，返回错误
MAX_LINES = 2000

# 文件大小上限（字节）：约 1MB
MAX_FILE_SIZE = 1_000_000


class ReadFile(BaseTool):
    """读文件工具。

    给定文件路径，返回带行号的文本内容。
    用于让模型了解代码结构、配置内容等。

    保护措施：
    - 文件大小超过 1MB 时拒绝读取
    - 行数超过 2000 行时截断并提示
    - 二进制文件检测（含 null 字节则拒绝）
    """

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def display_name(self) -> str:
        """用户可读的工具名称。"""
        return "读取文件"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file at the given path. "
            "Returns the file content with line numbers (format: 'LINE:\\tCONTENT'). "
            "Use this tool when you need to look at the contents of a file "
            "to understand code, configuration, or any text-based file. "
            "The file path can be absolute or relative to the current working directory. "
            "If the file does not exist or cannot be read, an error message is returned."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "The path to the file to read. "
                        "Can be absolute or relative to the working directory."
                    ),
                }
            },
            "required": ["path"],
        }

    def format_params(self, arguments: dict) -> str:
        """只显示文件路径作为参数摘要。"""
        return arguments.get("path", "")

    def format_result(self, result: ToolResult) -> str:
        """提取行数、大小信息生成摘要。"""
        if not result.success:
            return result.content[:80]
        # 从 content 中统计行数（带行号格式："1234\t...")
        lines = result.content.split("\n")
        filtered = [l for l in lines if l and not l.startswith("[结果已截断")]
        line_count = len(filtered)
        char_count = len(result.content)
        if "[结果已截断" in result.content:
            return f"已截断显示 {line_count} 行，共 {char_count} 字符"
        return f"{line_count} 行，{char_count} 字符"

    async def execute(self, arguments: dict) -> ToolResult:
        """读取文件内容。

        Args:
            arguments: 必须包含 "path" 键——要读取的文件路径

        Returns:
            ToolResult: 成功时 content 为带行号的文本，失败时 content 为错误描述
        """
        call_id = arguments.get("call_id", "")
        path = arguments.get("path", "")

        # ---- 参数校验：path 为空时给出诊断信息 ----
        if not path:
            return ToolResult.fail(
                call_id,
                self.name,
                f"参数错误：未提供文件路径（path 为空）。"
                f"收到的完整参数：{arguments}",
            )

        # ---- 检查文件是否存在 ----
        if not os.path.exists(path):
            return ToolResult.fail(
                call_id,
                self.name,
                f"文件不存在：{path}",
            )

        # ---- 检查是否为文件（排除目录） ----
        if not os.path.isfile(path):
            return ToolResult.fail(
                call_id,
                self.name,
                f"路径不是文件：{path}",
            )

        # ---- 检查文件大小 ----
        try:
            file_size = os.path.getsize(path)
            if file_size > MAX_FILE_SIZE:
                return ToolResult.fail(
                    call_id,
                    self.name,
                    f"文件过大（{file_size / 1_000_000:.1f}MB），"
                    f"上限为 {MAX_FILE_SIZE / 1_000_000:.0f}MB",
                )
        except OSError as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"无法获取文件大小：{e}",
            )

        # ---- 读取文件（先以二进制模式检测，再解码） ----
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read()
        except PermissionError:
            return ToolResult.fail(
                call_id,
                self.name,
                f"没有权限读取文件：{path}",
            )
        except Exception as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"读取文件失败：{e}",
            )

        # ---- 检测二进制内容（在原始字节层面检查 null 字节） ----
        if b"\x00" in raw_bytes:
            return ToolResult.fail(
                call_id,
                self.name,
                f"文件似乎为二进制文件（含 null 字节）：{path}",
            )

        # ---- 解码为 UTF-8 文本 ----
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail(
                call_id,
                self.name,
                f"无法以 UTF-8 编码读取文件（可能是二进制文件）：{path}",
            )

        # ---- 格式化输出：带行号 ----
        # 使用 splitlines() 正确处理各种换行符，且不产生末尾空行
        lines = content.splitlines()
        total_lines = len(lines)

        # 行数过多时截断并提示
        if total_lines > MAX_LINES:
            lines = lines[:MAX_LINES]
            truncated_msg = (
                f"\n\n[结果已截断：文件共 {total_lines} 行，"
                f"仅显示前 {MAX_LINES} 行]"
            )
        else:
            truncated_msg = ""

        # 行号格式：固定 4 位宽度 + 制表符 + 内容
        numbered = "\n".join(
            f"{i + 1:4d}\t{line}" for i, line in enumerate(lines)
        )

        return ToolResult.ok(
            call_id,
            self.name,
            numbered + truncated_msg,
        )

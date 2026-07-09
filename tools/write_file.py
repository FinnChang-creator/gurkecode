"""写文件工具

将指定内容写入文件（覆盖模式），父目录不存在时自动创建。
用于让模型产出代码文件、配置等。
"""

import os

from tools.base import BaseTool, ToolResult


class WriteFile(BaseTool):
    """写文件工具。

    给定文件路径和内容，覆盖写入文件。
    父目录不存在时自动递归创建。

    注意：
    - 以 UTF-8 编码写入
    - 会覆盖已存在的文件（不追加）
    """

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def display_name(self) -> str:
        """用户可读的工具名称。"""
        return "写入文件"

    @property
    def description(self) -> str:
        return (
            "Write content to a file at the given path. "
            "If the file already exists, it will be overwritten. "
            "If the parent directory does not exist, it will be created automatically. "
            "Use this tool when you need to create or overwrite a file "
            "with specific content, such as writing code, configuration, or documentation. "
            "The path can be absolute or relative to the current working directory."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["path", "content"],
        }

    def format_params(self, arguments: dict) -> str:
        """只显示文件路径作为参数摘要。"""
        return arguments.get("path", "")

    def format_result(self, result: ToolResult) -> str:
        """简洁显示写入结果。"""
        if not result.success:
            return result.content[:80]
        return result.content  # 写入工具的成功消息已经很简洁了

    async def execute(self, arguments: dict) -> ToolResult:
        """写入文件内容。

        Args:
            arguments: 必须包含 "path"（目标路径）和 "content"（写入内容）

        Returns:
            ToolResult: 成功时 content 包含写入摘要，失败时 content 为错误描述
        """
        call_id = arguments.get("call_id", "")
        path = arguments.get("path", "")
        content = arguments.get("content", "")

        if not path:
            return ToolResult.fail(
                call_id,
                self.name,
                "path 参数不能为空",
            )

        # ---- 确保父目录存在 ----
        try:
            parent_dir = os.path.dirname(os.path.abspath(path))
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
        except OSError as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"创建父目录失败：{e}",
            )

        # ---- 写入文件 ----
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return ToolResult.fail(
                call_id,
                self.name,
                f"没有权限写入文件：{path}",
            )
        except IsADirectoryError:
            return ToolResult.fail(
                call_id,
                self.name,
                f"路径指向一个目录，无法写入：{path}",
            )
        except Exception as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"写入文件失败：{e}",
            )

        # ---- 返回成功摘要 ----
        line_count = content.count("\n") + 1 if content else 0
        char_count = len(content)

        return ToolResult.ok(
            call_id,
            self.name,
            f"已写入 {path}（{line_count} 行，{char_count} 字符）",
        )

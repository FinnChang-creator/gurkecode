"""改文件工具

通过精确字符串匹配替换来修改文件内容。
只做唯一匹配替换——匹配 0 次或多于 1 次时返回错误，
确保修改的可控性和精确性。
"""

from tools.base import BaseTool, ToolResult


class EditFile(BaseTool):
    """改文件工具。

    给定文件路径、原始文本片段和新文本片段，
    在文件中查找原始片段的唯一出现并替换为新片段。

    约束：
    - 原始片段必须在文件中恰好出现一次（唯一匹配）
    - 匹配 0 次：返回错误，建议模型重新检查
    - 匹配多于 1 次：返回错误并列出匹配次数，模型需提供更精确的上下文
    """

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def display_name(self) -> str:
        """用户可读的工具名称。"""
        return "编辑文件"

    @property
    def description(self) -> str:
        return (
            "IMPORTANT: You MUST call read_file first to read the file's current content "
            "before editing. Never edit a file you haven't read. "
            "Replace a specific string in a file with a new string. "
            "The old_string must match exactly once in the file — "
            "if it matches zero times or more than once, the edit is rejected "
            "with a clear error message telling you how many matches were found. "
            "This ensures precise, controlled edits. "
            "Use this tool when you need to modify a specific section of a file "
            "without rewriting the entire file. "
            "Provide enough context in old_string to make it unique."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to find and replace. "
                        "Must match exactly once in the file. "
                        "Include surrounding context to ensure uniqueness."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The text to replace old_string with. "
                        "Use an empty string to delete old_string."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    def format_params(self, arguments: dict) -> str:
        """只显示文件路径作为参数摘要。"""
        return arguments.get("path", "")

    def format_result(self, result: ToolResult) -> str:
        """简洁显示编辑结果。"""
        if not result.success:
            return result.content[:80]
        return result.content  # 编辑工具的成功消息已经很简洁了

    async def execute(self, arguments: dict) -> ToolResult:
        """执行精确匹配替换。

        Args:
            arguments: 必须包含 "path"、"old_string"、"new_string"

        Returns:
            ToolResult: 成功时 content 为替换摘要，
                       失败时 content 说明匹配次数
        """
        call_id = arguments.get("call_id", "")
        path = arguments.get("path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")

        if not path:
            return ToolResult.fail(call_id, self.name, "path 参数不能为空")

        # ---- 读取文件 ----
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return ToolResult.fail(
                call_id, self.name, f"文件不存在：{path}"
            )
        except PermissionError:
            return ToolResult.fail(
                call_id, self.name, f"没有权限读取文件：{path}"
            )
        except Exception as e:
            return ToolResult.fail(
                call_id, self.name, f"读取文件失败：{e}"
            )

        # ---- 查找 old_string 的匹配次数 ----
        count = content.count(old_string)

        if count == 0:
            return ToolResult.fail(
                call_id,
                self.name,
                f"未找到匹配的文本片段。"
                f"old_string 在文件中未出现，请检查内容是否正确。",
            )
        elif count > 1:
            return ToolResult.fail(
                call_id,
                self.name,
                f"找到 {count} 处匹配，但必须唯一匹配。"
                f"请在 old_string 中包含更多上下文以精确定位。",
            )

        # ---- 唯一匹配：执行替换 ----
        new_content = content.replace(old_string, new_string, 1)

        # ---- 写回文件 ----
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except PermissionError:
            return ToolResult.fail(
                call_id, self.name, f"没有权限写入文件：{path}"
            )
        except Exception as e:
            return ToolResult.fail(
                call_id, self.name, f"写入文件失败：{e}"
            )

        # ---- 返回成功摘要 ----
        # 截取 old_string 前 60 字符用于显示
        preview = old_string[:60].replace("\n", "\\n")
        if len(old_string) > 60:
            preview += "..."

        return ToolResult.ok(
            call_id,
            self.name,
            f"已替换 {path} 中的 '{preview}'（1 处匹配）",
        )

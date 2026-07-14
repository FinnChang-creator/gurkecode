"""搜代码内容工具

在文件中搜索匹配指定模式的内容行，
返回命中位置（文件路径、行号、行内容）。
支持可选路径过滤，缩小搜索范围。
"""

import re
from pathlib import Path

from tools.base import BaseTool, ToolResult


# 搜索结果条数上限
MAX_RESULTS = 50


class GrepSearch(BaseTool):
    """搜代码内容工具。

    在文件内容中搜索匹配模式的行，返回命中位置信息。
    支持正则表达式匹配，可选路径范围过滤。

    保护措施：
    - 跳过二进制文件
    - 跳过过大文件（>1MB）
    - 结果条数有上限
    """

    @property
    def is_read_only(self) -> bool:
        """仅搜索文件内容，不修改文件系统。"""
        return True

    @property
    def name(self) -> str:
        return "grep_search"

    @property
    def display_name(self) -> str:
        """用户可读的工具名称。"""
        return "搜索内容"

    @property
    def description(self) -> str:
        return (
            "Search for a pattern in file contents. "
            "Returns matching lines with file path, line number, and line content. "
            "Supports regular expressions for the search pattern. "
            "Optionally filter by a path pattern to narrow the search scope. "
            "Use this tool to find usages of functions, classes, strings, "
            "or any other text pattern in the codebase. "
            "Results are limited to {} matches.".format(MAX_RESULTS)
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "The text or regex pattern to search for. "
                        "Examples: 'def main', 'import os', 'TODO'"
                    ),
                },
                "path_filter": {
                    "type": "string",
                    "description": (
                        "Optional glob pattern to filter which files to search. "
                        "Examples: '**/*.py', 'src/**/*.js'. "
                        "If not provided, searches all text files."
                    ),
                },
            },
            "required": ["pattern"],
        }

    def format_params(self, arguments: dict) -> str:
        """显示搜索模式作为参数摘要。"""
        return arguments.get("pattern", "")

    def format_result(self, result: ToolResult) -> str:
        """提取匹配数量生成摘要。"""
        if not result.success:
            return result.content[:80]
        # content 第一行就是 "找到 N 条匹配..." 的摘要
        first_line = result.content.split("\n")[0]
        return first_line

    async def execute(self, arguments: dict) -> ToolResult:
        """搜索文件内容。

        Args:
            arguments: 必须包含 "pattern"，可选 "path_filter"

        Returns:
            ToolResult: content 为格式化的搜索结果
        """
        call_id = arguments.get("call_id", "")
        pattern = arguments.get("pattern", "")
        path_filter = arguments.get("path_filter")

        if not pattern:
            return ToolResult.fail(
                call_id, self.name, "pattern 参数不能为空"
            )

        # ---- 编译正则 ----
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"无效的正则表达式：{e}",
            )

        # ---- 收集要搜索的文件 ----
        cwd = Path.cwd()

        if path_filter:
            try:
                files = sorted(
                    p for p in cwd.glob(path_filter)
                    if p.is_file() and not _is_hidden_or_ignored(p)
                )
            except Exception as e:
                return ToolResult.fail(
                    call_id,
                    self.name,
                    f"路径过滤失败：{e}",
                )
        else:
            # 没有路径过滤：搜索所有非隐藏文本文件
            files = sorted(
                p for p in cwd.rglob("*")
                if p.is_file()
                and not _is_hidden_or_ignored(p)
            )

        # ---- 搜索 ----
        results = []
        for file_path in files:
            # 至少有一个结果才继续读更多文件
            if len(results) >= MAX_RESULTS:
                break

            # 跳过过大文件
            try:
                if file_path.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except (PermissionError, IsADirectoryError, UnicodeDecodeError):
                continue
            except Exception:
                continue

            # 检测二进制
            if "\x00" in content:
                continue

            # 逐行搜索
            for line_no, line in enumerate(content.split("\n"), 1):
                if len(results) >= MAX_RESULTS:
                    break
                if regex.search(line):
                    rel_path = str(file_path.relative_to(cwd))
                    # 截断过长的行
                    display_line = line[:200]
                    if len(line) > 200:
                        display_line += "..."
                    results.append(f"{rel_path}:{line_no}: {display_line}")

        # ---- 格式化输出 ----
        if not results:
            return ToolResult.ok(
                call_id,
                self.name,
                f"未找到匹配 '{pattern}' 的内容",
            )

        total = len(results)
        truncated = total >= MAX_RESULTS
        summary = (
            f"找到 {total}+ 条匹配 '{pattern}' 的结果：\n\n"
            if truncated
            else f"找到 {total} 条匹配 '{pattern}' 的结果：\n\n"
        )
        return ToolResult.ok(
            call_id,
            self.name,
            summary + "\n".join(results),
        )


def _is_hidden_or_ignored(path: Path) -> bool:
    """检查路径是否应被排除。

    复用与 GlobSearch 相同的排除逻辑。

    Args:
        path: 文件路径

    Returns:
        True 表示应被排除
    """
    for part in path.parts:
        if part.startswith(".") and part != ".":
            return True
        if part in ("__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"):
            return True
    return False

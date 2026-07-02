"""按模式找文件工具

使用 glob 模式匹配文件系统中的文件路径。
返回匹配路径列表，适合模型在大致知道文件位置但不确定确切路径时使用。
"""

from pathlib import Path

from tools.base import BaseTool, ToolResult


# 搜索结果条数上限
MAX_RESULTS = 50


class GlobSearch(BaseTool):
    """按模式找文件工具。

    给定 glob 模式，返回匹配的文件路径列表。
    支持标准 glob 语法：
    - `*` 匹配单段路径中的任意字符
    - `**` 递归匹配任意层级
    - `?` 匹配单个字符
    - `[...]` 字符类

    只匹配文件，不包含目录。
    """

    @property
    def name(self) -> str:
        return "glob_search"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern. "
            "Returns a list of matching file paths. "
            "Use this tool when you need to locate files by name patterns, "
            "such as finding all Python files ('**/*.py'), "
            "configuration files ('*.yaml'), or files in a specific directory. "
            "Only files are returned, not directories. "
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
                        "The glob pattern to match file paths. "
                        "Examples: '**/*.py', '*.yaml', 'tools/*.py'"
                    ),
                }
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        """按 glob 模式查找文件。

        Args:
            arguments: 必须包含 "pattern"——glob 匹配模式

        Returns:
            ToolResult: content 为匹配的文件路径列表（每行一个）
        """
        call_id = arguments.get("call_id", "")
        pattern = arguments.get("pattern", "")

        if not pattern:
            return ToolResult.fail(
                call_id, self.name, "pattern 参数不能为空"
            )

        # ---- 执行 glob 搜索 ----
        try:
            cwd = Path.cwd()
            matches = sorted(
                p for p in cwd.glob(pattern)
                if p.is_file() and not _is_hidden_or_ignored(p)
            )
        except Exception as e:
            return ToolResult.fail(
                call_id,
                self.name,
                f"glob 搜索失败：{e}",
            )

        # ---- 格式化输出 ----
        if not matches:
            return ToolResult.ok(
                call_id,
                self.name,
                f"未找到匹配 '{pattern}' 的文件",
            )

        # 转为相对路径
        relative_paths = [str(m.relative_to(cwd)) for m in matches]

        total = len(relative_paths)
        if total > MAX_RESULTS:
            shown = relative_paths[:MAX_RESULTS]
            result = "\n".join(shown)
            result += f"\n\n[结果已截断：共 {total} 个匹配，仅显示前 {MAX_RESULTS} 个]"
        else:
            result = "\n".join(relative_paths)

        return ToolResult.ok(
            call_id,
            self.name,
            f"找到 {total} 个匹配 '{pattern}' 的文件：\n\n{result}",
        )


def _is_hidden_or_ignored(path: Path) -> bool:
    """检查路径是否应被排除。

    排除以 . 开头的隐藏文件/目录，
    以及常见的 VCS/构建目录。

    Args:
        path: 文件路径

    Returns:
        True 表示应被排除
    """
    # 排除路径中任意部分以 . 开头（隐藏文件/目录）
    for part in path.parts:
        if part.startswith(".") and part != ".":
            return True
        # 排除常见忽略目录
        if part in ("__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build"):
            return True

    return False

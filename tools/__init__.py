"""工具系统

暴露全局工具注册中心实例，所有核心工具已在导入时自动注册。
外部模块通过 `from tools import registry` 获取并使用。
"""

from tools.registry import ToolRegistry

# ---- 创建全局注册中心 ----
registry = ToolRegistry()

# ---- 注册全部六个核心工具 ----
from tools.read_file import ReadFile
from tools.write_file import WriteFile
from tools.edit_file import EditFile
from tools.bash import Bash
from tools.glob_search import GlobSearch
from tools.grep_search import GrepSearch

registry.register(ReadFile())
registry.register(WriteFile())
registry.register(EditFile())
registry.register(Bash())
registry.register(GlobSearch())
registry.register(GrepSearch())

# 清理命名空间：只暴露 registry
__all__ = ["registry"]

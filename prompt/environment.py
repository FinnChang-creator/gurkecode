"""环境信息采集

收集运行环境信息（工作目录、平台、日期、git 状态、应用版本、当前模型），
构造成一段供模型感知环境的文本。

采集过程快速且有界，任一项取不到时降级（留空/省略），不中断会话。
"""

import asyncio
import datetime
import os
import platform as plat
from dataclasses import dataclass

# gurkecode 版本号，后续可从 pyproject.toml 或 package metadata 动态读取
_APP_VERSION = "0.1.0"

# git 命令超时（秒）：防止因 git 锁文件或大仓库导致卡顿
_GIT_TIMEOUT = 5


@dataclass
class EnvironmentInfo:
    """运行环境快照，用于构造系统提示第二段。

    所有字段均为字符串。采集不到时字段为空字符串，
    format_environment() 会自动跳过空字段。

    Attributes:
        cwd: 当前工作目录绝对路径
        platform: 操作系统标识，如 "Linux 6.8.0-101-generic"
        date: 当前日期，ISO 格式如 "2026-07-14"
        git_branch: 当前分支名，非 git 目录或 git 不可用时为空
        git_status: git 状态摘要（"clean" / "N files changed"），非 git 时为空
        app_version: gurkecode 版本号
        model: 当前使用的模型名（来自 ProviderConfig.model）
    """

    cwd: str = ""
    platform: str = ""
    date: str = ""
    git_branch: str = ""
    git_status: str = ""
    app_version: str = ""
    model: str = ""


async def _run_git(*args: str) -> str:
    """执行 git 命令并返回 stdout 首行（去换行）。

    超时或 git 不可用时返回空字符串，不抛异常。

    Args:
        *args: git 子命令参数，如 "branch", "--show-current"

    Returns:
        命令输出首行（去尾部换行），失败返回 ""
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_GIT_TIMEOUT
        )
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
        return ""
    except Exception:
        # 任何异常（git 未安装、目录不是仓库、超时等）降级为空
        return ""


async def _git_branch() -> str:
    """获取当前 git 分支名。

    Returns:
        分支名，失败返回 ""
    """
    return await _run_git("branch", "--show-current")


async def _git_status() -> str:
    """获取 git 工作区状态摘要。

    Returns:
        - "clean" — 无变更
        - "N files changed" — N 为 porcelain 输出行数
        - "" — git 不可用
    """
    output = await _run_git("status", "--porcelain")
    if output == "":
        # git 可用且无输出 = 干净
        # 区分"git 不可用"（_run_git 返回 ""）和"干净"（porcelain 返回 ""）
        # 需要再跑一次确认 git 是否可用
        if not await _run_git("rev-parse", "--git-dir"):
            return ""  # git 不可用
        return "clean"
    # 统计行数作为变更文件数
    count = len([line for line in output.split("\n") if line.strip()])
    return f"{count} file{'s' if count != 1 else ''} changed"


async def collect_environment(model: str) -> EnvironmentInfo:
    """采集当前运行环境的各项信息。

    每个采集步骤独立降级：任一项失败不影响其他项，
    不抛异常、不阻塞界面。

    Args:
        model: 当前使用的模型名称（从 ProviderConfig.model 传入）

    Returns:
        EnvironmentInfo 实例，所有字段均已填充（失败时为空）
    """
    # 同步采集（不涉及 IO 的字段）
    cwd = os.getcwd()
    platform_str = plat.platform()
    date_str = datetime.date.today().isoformat()

    # 异步采集 git 信息（并行执行，不等待串行）
    branch, git_status = await asyncio.gather(
        _git_branch(),
        _git_status(),
    )

    return EnvironmentInfo(
        cwd=cwd,
        platform=platform_str,
        date=date_str,
        git_branch=branch,
        git_status=git_status,
        app_version=_APP_VERSION,
        model=model,
    )


def format_environment(info: EnvironmentInfo) -> str:
    """将环境信息格式化为文本块，作为系统提示第二段呈现。

    用 <environment> 标签包裹，每条信息一行。
    空字段自动跳过对应行。

    Args:
        info: 已采集的环境信息

    Returns:
        格式化的环境文本块，如：
        <environment>
        Working directory: /home/user/project
        Platform: Linux 6.8.0-101-generic
        Date: 2026-07-14
        Git branch: main
        Git status: clean
        App version: 0.1.0
        Model: claude-sonnet-4-6
        </environment>
    """
    lines = ["<environment>"]

    # 每条信息按固定顺序呈现，空值跳过
    _add_if(lines, "Working directory", info.cwd)
    _add_if(lines, "Platform", info.platform)
    _add_if(lines, "Date", info.date)
    _add_if(lines, "Git branch", info.git_branch)
    _add_if(lines, "Git status", info.git_status)
    _add_if(lines, "App version", info.app_version)
    _add_if(lines, "Model", info.model)

    lines.append("</environment>")
    return "\n".join(lines)


def _add_if(lines: list[str], label: str, value: str) -> None:
    """辅助：value 非空时追加一行 "label: value"。

    Args:
        lines: 目标行列表（原地修改）
        label: 字段标签
        value: 字段值，为空字符串时跳过
    """
    if value:
        lines.append(f"{label}: {value}")

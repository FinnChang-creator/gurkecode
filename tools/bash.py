"""执行命令工具

在 shell 中执行用户/模型提交的命令，受超时约束。
返回 stdout、stderr 和退出码的结构化结果。
"""

import asyncio

from tools.base import BaseTool, ToolResult


# 命令执行的超时时间（秒）
BASH_TIMEOUT = 30

# 输出截断上限（字符数）
OUTPUT_MAX_CHARS = 10_000


class Bash(BaseTool):
    """执行命令工具。

    在 shell 中执行给定的命令，受超时约束。
    - 超时：自动终止进程并返回超时结果
    - 输出截断：超过上限时截断并提示
    - 非零退出码不算失败（模型的命令可能非零退出），
      只将超时、异常等情况标记为失败

    安全注意：
    当前版本不做路径白名单或沙箱限制（按 spec 暂不做）。
    """

    @property
    def name(self) -> str:
        return "bash"

    @property
    def display_name(self) -> str:
        """用户可读的工具名称。"""
        return "执行命令"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in the current working directory. "
            "Returns the standard output, standard error, and exit code. "
            "The command has a timeout of {} seconds — "
            "if it runs longer, it will be terminated and a timeout error returned. "
            "Use this tool for running development commands (build, test, lint), "
            "inspecting the environment, or any other shell operation. "
            "Long-running servers and interactive programs are not suitable."
        ).format(BASH_TIMEOUT)

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                }
            },
            "required": ["command"],
        }

    def format_params(self, arguments: dict) -> str:
        """截断显示命令字符串作为参数摘要。"""
        cmd = arguments.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:60] + "..."
        return cmd

    def format_result(self, result: ToolResult) -> str:
        """提取退出码和输出长度信息生成摘要。"""
        if not result.success:
            return result.content[:80]
        # 从 content 中提取退出码（格式："[退出码: N]"）
        for line in result.content.split("\n"):
            if line.startswith("[退出码:"):
                return line
        return "已完成"

    async def execute(self, arguments: dict) -> ToolResult:
        """执行 shell 命令。

        Args:
            arguments: 必须包含 "command"——要执行的命令字符串

        Returns:
            ToolResult: content 包含 stdout、stderr 和退出码
        """
        call_id = arguments.get("call_id", "")
        command = arguments.get("command", "")

        if not command:
            return ToolResult.fail(
                call_id, self.name, "command 参数不能为空"
            )

        # ---- 启动子进程 ----
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            return ToolResult.fail(
                call_id, self.name, f"无法启动进程：{e}"
            )

        # ---- 等待完成（带超时） ----
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=BASH_TIMEOUT
            )
        except asyncio.TimeoutError:
            # 超时：终止进程
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass  # 进程可能已经不存在
            return ToolResult.fail(
                call_id,
                self.name,
                f"命令执行超时（{BASH_TIMEOUT} 秒）：{command}",
            )

        # ---- 解码输出 ----
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode

        # ---- 截断过长输出 ----
        stdout_truncated = False
        if len(stdout) > OUTPUT_MAX_CHARS:
            stdout = stdout[:OUTPUT_MAX_CHARS]
            stdout_truncated = True

        stderr_truncated = False
        if len(stderr) > OUTPUT_MAX_CHARS:
            stderr = stderr[:OUTPUT_MAX_CHARS]
            stderr_truncated = True

        # ---- 组装结果 ----
        parts = []

        if stdout:
            parts.append(f"[stdout]\n{stdout}")
            if stdout_truncated:
                parts.append("[stdout 已截断]")

        if stderr:
            parts.append(f"[stderr]\n{stderr}")
            if stderr_truncated:
                parts.append("[stderr 已截断]")

        parts.append(f"[退出码: {exit_code}]")

        return ToolResult.ok(
            call_id,
            self.name,
            "\n".join(parts) if parts else f"[退出码: {exit_code}]",
        )

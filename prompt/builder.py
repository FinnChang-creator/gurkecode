"""模块化系统提示拼装器

将全局指令按职责拆成模块（Section），
每个模块带名称、优先级、内容。
装配时按优先级从高到低拼接，模块间以空行分隔。

提供工厂函数 create_default_sections() 创建含 7 个固定模块 + 3 个空槽的 builder，
新增指令只需定义新模块挂到对应优先级，不改装配主逻辑。
"""

from dataclasses import dataclass


@dataclass
class Section:
    """系统提示的一个模块。

    每个模块代表一类指令（身份、工具使用、语气等），
    按优先级从高到低拼接成完整系统提示。

    Attributes:
        name: 模块名称，如 "身份"、"工具使用"，用于调试和日志
        priority: 优先级，数值越小越靠前（1=身份, 2=系统约束, ...）
        content: 模块内容文本，为空时装配阶段跳过（空槽机制）
    """

    name: str
    priority: int
    content: str


class PromptBuilder:
    """收集 Section，按优先级拼接成最终系统提示。

    使用方式：
        builder = PromptBuilder()
        builder.add(Section("身份", 1, "You are gurkecode..."))
        builder.add(Section("工具使用", 5, "You have access to tools..."))
        stable_system = builder.build()

    build() 的行为：
    - 按 priority 升序排列
    - content 为空（含仅空白字符）的 Section 跳过
    - 模块间以两个换行符分隔
    - 结果逐字节确定——同一组 Section、同一 build 调用 = 同一输出
    """

    def __init__(self) -> None:
        """初始化空的模块收集器。"""
        self._sections: list[Section] = []

    def add(self, section: Section) -> None:
        """追加一个模块。

        模块按添加时的 priority 排序，
        同名模块不覆盖——调用方自行保证唯一性。

        Args:
            section: 要追加的 Section 实例
        """
        self._sections.append(section)

    def build(self) -> str:
        """按优先级拼接所有模块为完整系统提示文本。

        过滤规则：
        - content.strip() 为空的 Section 被跳过（空槽机制）
        - 剩余 Section 按 priority 升序排列
        - 模块间以 \\n\\n 分隔

        Returns:
            拼接后的完整系统提示字符串，逐字节确定
        """
        # 过滤空模块（content 为空或仅含空白字符）
        non_empty = [
            s for s in self._sections if s.content.strip()
        ]
        # 按优先级升序排列
        non_empty.sort(key=lambda s: s.priority)
        # 用 \\n\\n 连接各模块内容
        return "\n\n".join(s.content for s in non_empty)


# ---------------------------------------------------------------------------
# 固定模块内容定义
# ---------------------------------------------------------------------------
# 以下 7 个模块从原有 SYSTEM_PROMPT / TOOLS_APPEND_PROMPT 拆解而来，
# 各自独立，按优先级编号。新增指令只需新增对应优先级的 Section 并 add。

_SECTION_IDENTITY = """\
You are gurkecode, an AI assistant running in the terminal.

You are designed to help users with software engineering tasks — reading, writing, \
and reasoning about code. You have access to the current working directory and can \
discuss files, architecture, and implementation details.

The user is a developer working at the command line. Adapt your responses accordingly."""

_SECTION_SYSTEM_CONSTRAINTS = """\
You have access to tools that let you read files, write files, edit files, \
execute shell commands, search for files by pattern, and search file contents. \
When you need information that a tool can provide, use it — don't guess. \
When the user asks you to perform an action (read a file, run a command, etc.), \
use the appropriate tool immediately."""

_SECTION_TASK_MODE = """\
You operate in normal execution mode. When given a task, analyze the request, \
gather necessary information using tools, and then execute the required actions. \
After receiving tool results, use them to give a complete, accurate answer. \
You may call tools across multiple rounds until the task is complete — \
each round you can request new tools based on previous results. \
When you have enough information, provide your final text answer \
without requesting additional tools."""

_SECTION_ACTION = """\
When you have enough information to act, act. Do not re-derive facts already \
established in the conversation, re-litigate a decision the user has already made, \
or narrate options you will not pursue. If you are weighing a choice, give a \
recommendation, not an exhaustive survey."""

_SECTION_TOOL_USAGE = """\
When using tools:
- IMPORTANT: PREFER dedicated tools (read_file, edit_file, write_file, glob_search, \
grep_search) over shell commands when they achieve the same result. \
Use bash only when dedicated tools cannot accomplish the task.
- IMPORTANT: You MUST read a file before editing it — never edit a file \
you haven't read first. The file may have changed since you last saw it.
- After receiving tool results, evaluate whether you need more information. \
If so, call additional tools in the next round.
- When you have all the information needed, provide your final response \
without requesting additional tools.
- Reference file paths and line numbers when discussing code."""

_SECTION_TONE = """\
Be concise and direct. When discussing code, reference file paths and line numbers. \
Use markdown for code blocks, lists, and structured responses. \
The user is a developer — avoid explaining basic programming concepts \
unless asked."""

_SECTION_OUTPUT = """\
When responding:
- Use markdown code blocks with language identifiers for code snippets
- Reference code as `file_path:line_number` when possible
- Structure longer responses with headings and lists for readability"""

# 3 个空槽：content="" 表示暂不接入，build() 时自动跳过。
# 后续章节更改 content 来源即可激活，不改装配逻辑。

_SECTION_CUSTOM_INSTRUCTIONS = ""   # 优先级 8：自定义指令（后续从 CLAUDE.md 加载）
_SECTION_ACTIVATED_SKILLS = ""      # 优先级 9：已激活 Skill（后续接入 MCP 工具/资源）
_SECTION_LONG_TERM_MEMORY = ""      # 优先级 10：长期记忆（后续接入记忆写入与召回）


def create_default_sections() -> PromptBuilder:
    """创建含 7 个固定模块 + 3 个空槽的 PromptBuilder。

    固定模块按优先级排列（1-7）：
    1. 身份    — 告诉模型它是谁
    2. 系统约束 — 工具能力概述
    3. 任务模式 — 正常执行模式行为
    4. 动作执行 — 何时行动、何时停
    5. 工具使用 — 何时用什么工具、关键约定（双重强化）
    6. 语气风格 — 简洁直接
    7. 文本输出 — 格式规范

    可扩展空槽（8-10）：
    8. 自定义指令   — content=""，装配自动跳过
    9. 已激活 Skill — content=""，装配自动跳过
    10. 长期记忆    — content=""，装配自动跳过

    Returns:
        已填充好所有模块的 PromptBuilder，可直接 build()
    """
    builder = PromptBuilder()

    # ---- 固定模块（优先级 1-7） ----
    builder.add(Section("身份",       1, _SECTION_IDENTITY))
    builder.add(Section("系统约束",   2, _SECTION_SYSTEM_CONSTRAINTS))
    builder.add(Section("任务模式",   3, _SECTION_TASK_MODE))
    builder.add(Section("动作执行",   4, _SECTION_ACTION))
    builder.add(Section("工具使用",   5, _SECTION_TOOL_USAGE))
    builder.add(Section("语气风格",   6, _SECTION_TONE))
    builder.add(Section("文本输出",   7, _SECTION_OUTPUT))

    # ---- 可扩展空槽（优先级 8-10） ----
    builder.add(Section("自定义指令",   8, _SECTION_CUSTOM_INSTRUCTIONS))
    builder.add(Section("已激活 Skill", 9, _SECTION_ACTIVATED_SKILLS))
    builder.add(Section("长期记忆",     10, _SECTION_LONG_TERM_MEMORY))

    return builder

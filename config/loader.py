"""配置加载器

从 YAML 配置文件和系统环境变量中加载服务商配置列表。
主要包括两个来源：
1. 项目根目录的 gurkecode.yaml 文件中的 providers 列表
2. 若环境变量 ANTHROPIC_API_KEY 非空，自动追加一个 Anthropic 协议的 provider
"""

import os
import re
import sys
from pathlib import Path

import yaml

from config.models import ProviderConfig
from config.validator import ConfigError, validate_providers


# 环境变量名前缀，用于从环境变量构建 provider
# Claude Code 使用 ANTHROPIC_AUTH_TOKEN，Claude API 使用 ANTHROPIC_API_KEY
# 按优先级检查：AUTH_TOKEN 优先（Claude Code 环境），其次 API_KEY
_ENV_ANTHROPIC_KEY_NAMES = ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"]
# 环境变量 provider 的默认模型名
_ENV_DEFAULT_MODEL = "claude-sonnet-4-6"

# ANSI 转义序列正则（用于清洗环境变量值中可能混入的 ANSI 控制码）
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# 合法模型名字符集（字母、数字、连字符、点、下划线、冒号）
_MODEL_NAME_RE = re.compile(r"[a-zA-Z0-9\-._:]+")


def _sanitize_env_value(value: str) -> str:
    """清洗环境变量值。

    移除可能混入的 ANSI 转义序列（终端控制码泄漏到环境变量中）。
    仅保留可打印文本。

    处理两种情况：
    1. 真正的 ANSI 转义序列（如 \\x1b[1m, \\x1b[0m）
    2. 字面残留的控制序列标记（如 [1m], [0m）——当终端驱动将 ESC 字符
       丢失后留下的括号片段

    Args:
        value: 原始环境变量值

    Returns:
        清洗后的字符串
    """
    if not value:
        return value
    # 1. 移除真正的 ANSI 转义序列（ESC [ ... m/字母）
    value = _ANSI_ESCAPE_RE.sub("", value)
    # 2. 移除残留的字面 ANSI 参数片段（如 "[1m]", "[0m"）
    #    这些是 ANSI 转义码在 ESC 字符丢失后留下的括号部分
    value = re.sub(r"\[\d+(;\d+)*m\]?", "", value)
    # 3. 去除首尾空白
    value = value.strip()
    return value


def load_providers(config_path: str) -> list[ProviderConfig]:
    """加载并校验所有服务商配置。

    从给定的 YAML 配置文件中读取 providers 列表，
    然后检查 ANTHROPIC_API_KEY 环境变量是否需要追加一个额外的 provider。
    所有 provider 加载完毕后统一校验。

    Args:
        config_path: YAML 配置文件的路径（通常是 "gurkecode.yaml"）

    Returns:
        校验通过的服务商配置列表

    Raises:
        ConfigError: 配置文件不存在、YAML 格式错误、或必要字段缺失时抛出
        SystemExit: 严重错误时直接退出（如配置文件不存在）
    """
    providers: list[ProviderConfig] = []

    # ---- 1. 从 YAML 文件加载 ----
    config_file = Path(config_path)
    if not config_file.exists():
        print(
            f"错误：配置文件 '{config_path}' 不存在。"
            f"请在项目根目录创建 gurkecode.yaml 文件。",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(
            f"错误：配置文件 '{config_path}' 的 YAML 格式有误。\n"
            f"详情：{e}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 配置文件可能为空或没有 providers 键
    if raw is None:
        raw = {}

    raw_providers = raw.get("providers", [])
    if raw_providers is None:
        raw_providers = []

    # 遍历 YAML 中的每项配置，构造 ProviderConfig 对象
    for i, item in enumerate(raw_providers):
        # 构造错误信息时用索引+1 方便用户定位
        item_num = i + 1
        try:
            provider = ProviderConfig(
                name=item.get("name", ""),
                protocol=item.get("protocol", ""),
                api_key=item.get("api_key", ""),
                model=item.get("model", ""),
                base_url=item.get("base_url"),      # None 表示用协议默认地址
                thinking=item.get("thinking", False),  # 默认不开启扩展思考
            )
            providers.append(provider)
        except Exception as e:
            print(
                f"错误：解析第 {item_num} 个 provider 配置时出错：{e}",
                file=sys.stderr,
            )
            sys.exit(1)

    # ---- 2. 检测环境变量中的 Anthropic 密钥 ----
    # 依次检查多个可能的密钥环境变量名（按优先级）
    env_api_key = ""
    for key_name in _ENV_ANTHROPIC_KEY_NAMES:
        candidate = _sanitize_env_value(os.environ.get(key_name, ""))
        if candidate:
            env_api_key = candidate
            break

    if env_api_key:
        # 检查是否与已有配置的 api_key 重复（简单去重，避免重复添加）
        duplicate = any(
            p.protocol == "anthropic" and p.api_key == env_api_key
            for p in providers
        )
        if not duplicate:
            # 清洗环境变量中的模型名（可能含 ANSI 残留）
            env_model = _sanitize_env_value(
                os.environ.get("ANTHROPIC_MODEL", "")
            )
            # 如果清洗后为空或不合法，回退到默认模型名
            if not env_model or not _MODEL_NAME_RE.fullmatch(env_model):
                env_model = _ENV_DEFAULT_MODEL

            env_base_url = _sanitize_env_value(
                os.environ.get("ANTHROPIC_BASE_URL", "")
            ) or None

            env_thinking_raw = _sanitize_env_value(
                os.environ.get("ANTHROPIC_THINKING", "")
            )
            env_thinking = env_thinking_raw.lower() in ("1", "true", "yes")

            env_provider = ProviderConfig(
                name="Claude (from env)",
                protocol="anthropic",
                api_key=env_api_key,
                model=env_model,
                base_url=env_base_url,
                thinking=env_thinking,
            )
            providers.append(env_provider)

    # ---- 3. 统一校验 ----
    try:
        validate_providers(providers)
    except ConfigError as e:
        print(f"错误：{e.message}", file=sys.stderr)
        sys.exit(1)

    return providers

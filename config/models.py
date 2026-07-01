"""配置数据模型

定义服务商配置的数据结构，供加载器和校验器使用。
"""

from dataclasses import dataclass


@dataclass
class ProviderConfig:
    """单个 LLM 服务商的配置。

    包含连接所需的全部信息：协议类型、认证密钥、模型名称、
    以及可选的自定义端点地址和扩展思考开关。

    Attributes:
        name: 可读的服务商名称，用于界面展示，如 "Claude via Anthropic"
        protocol: 协议类型，取值为 "anthropic" 或 "openai"
        api_key: API 认证密钥（注意：不应在日志或界面中回显）
        model: 模型名称，如 "claude-sonnet-4-6" 或 "gpt-4o"
        base_url: 自定义 API 端点地址，None 表示使用协议默认地址
        thinking: 是否开启扩展思考（extended thinking），仅 Anthropic 协议支持
    """

    name: str
    protocol: str
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False

    # 合法的协议类型常量
    PROTOCOL_ANTHROPIC = "anthropic"
    PROTOCOL_OPENAI = "openai"
    VALID_PROTOCOLS = {PROTOCOL_ANTHROPIC, PROTOCOL_OPENAI}

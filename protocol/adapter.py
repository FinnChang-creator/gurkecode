"""协议适配器工厂

根据 ProviderConfig 的 protocol 字段创建对应的 ChatProtocol 实例。
上层代码不需要知道具体协议实现类的存在，只需要调用 create_protocol()。
"""

from config.models import ProviderConfig
from protocol.anthropic import AnthropicProtocol
from protocol.models import ChatProtocol
from protocol.openai import OpenAIProtocol


def create_protocol(config: ProviderConfig) -> ChatProtocol:
    """根据配置创建对应的协议适配器实例。

    工厂函数，根据 ProviderConfig.protocol 的值选择具体实现：
    - "anthropic" → AnthropicProtocol
    - "openai"    → OpenAIProtocol

    Args:
        config: 已校验的服务商配置

    Returns:
        ChatProtocol 实例，可用于发起流式对话请求

    Raises:
        ValueError: protocol 字段值不是已知的协议类型
    """
    protocol_type = config.protocol.lower()

    if protocol_type == ProviderConfig.PROTOCOL_ANTHROPIC:
        return AnthropicProtocol(
            api_key=config.api_key,
            base_url=config.base_url,
        )
    elif protocol_type == ProviderConfig.PROTOCOL_OPENAI:
        return OpenAIProtocol(
            api_key=config.api_key,
            base_url=config.base_url,
        )
    else:
        # 理论上 config 已经过校验，这里作为兜底
        raise ValueError(
            f"不支持的协议类型：'{config.protocol}'。"
            f"支持的类型：{', '.join(sorted(ProviderConfig.VALID_PROTOCOLS))}。"
        )

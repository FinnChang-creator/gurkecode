"""配置校验器

对服务商配置进行逐项校验，确保所有必要字段存在且合法。
校验失败时抛出 ConfigError，由上层决定如何处理（通常是终止启动）。
"""

from config.models import ProviderConfig


class ConfigError(Exception):
    """配置错误异常。

    当服务商配置缺少必要字段、字段值不合法时抛出。
    消息应直接面向用户，可读且明确指出问题所在。

    Attributes:
        message: 面向用户的错误描述
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def validate_provider(provider: ProviderConfig) -> None:
    """校验单个服务商配置。

    检查以下必要项：
    - name 非空（仅空白字符也视为空）
    - protocol 为 "anthropic" 或 "openai"
    - api_key 非空（仅空白字符也视为空）
    - model 非空（仅空白字符也视为空）

    Args:
        provider: 待校验的服务商配置

    Raises:
        ConfigError: 任一校验项不通过时抛出，消息指明哪个字段有问题
    """
    # 校验 name 字段：必须存在且非空
    if not provider.name or not provider.name.strip():
        raise ConfigError(
            f"服务商配置缺少 name 字段。每项配置必须有一个可读名称。"
        )

    # 校验 protocol 字段：必须是已知的协议类型
    if provider.protocol not in ProviderConfig.VALID_PROTOCOLS:
        raise ConfigError(
            f"服务商 '{provider.name}' 的协议类型 '{provider.protocol}' 不合法。"
            f"支持的类型：{', '.join(sorted(ProviderConfig.VALID_PROTOCOLS))}。"
        )

    # 校验 api_key 字段：必须存在且非空（密钥不能是纯空白）
    if not provider.api_key or not provider.api_key.strip():
        raise ConfigError(
            f"服务商 '{provider.name}' 缺少 api_key。"
            f"请在配置文件中设置或通过环境变量提供。"
        )

    # 校验 model 字段：必须存在且非空
    if not provider.model or not provider.model.strip():
        raise ConfigError(
            f"服务商 '{provider.name}' 缺少 model 字段。"
            f"请指定要使用的模型名称。"
        )


def validate_providers(providers: list[ProviderConfig]) -> None:
    """校验所有服务商配置列表。

    Args:
        providers: 服务商配置列表

    Raises:
        ConfigError: 列表为空，或列表中任一项校验不通过
    """
    # 至少要有一个可用的服务商
    if not providers:
        raise ConfigError(
            "没有找到任何可用的服务商配置。"
            "请在 gurkecode.yaml 中配置至少一个 provider，"
            "或设置 ANTHROPIC_API_KEY 环境变量。"
        )

    # 逐项校验，遇到第一个不通过的就停止
    for provider in providers:
        validate_provider(provider)

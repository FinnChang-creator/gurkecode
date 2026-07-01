"""OpenAI 协议适配器

实现 OpenAI Chat Completions API 的流式对话。
参考：https://platform.openai.com/docs/api-reference/chat

SSE 事件流格式（每行以 "data: " 开头）：
- data: {"id":"...", "object":"chat.completion.chunk", "choices":[{"delta":{"content":"..."}, ...}]}
- data: [DONE]   ← 流结束标记

注意：OpenAI 兼容协议（如各类代理/网关）通常也使用此格式，
     通过自定义 base_url 即可接入。
"""

import json
from typing import AsyncIterator

import httpx

from protocol.models import ChatMessage, ChatProtocol, StreamEvent


# OpenAI Chat Completions API 默认端点
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com"


class OpenAIProtocol(ChatProtocol):
    """OpenAI Chat Completions API 协议适配器。

    负责将内部的 ChatMessage 列表转换为 OpenAI 请求格式、
    发起流式 HTTP 请求、并解析 SSE 响应为统一的 StreamEvent 序列。

    注意：
    - system 消息作为 messages 数组中的一个 role="system" 条目
    - OpenAI 没有原生的 extended thinking，thinking 参数被忽略
    - 兼容服务（如 Azure OpenAI、本地模型）通过自定义 base_url 接入
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        """初始化 OpenAI 协议适配器。

        Args:
            api_key: OpenAI API 密钥（格式通常为 sk-...）
            base_url: 自定义 API 端点地址，None 则使用默认 https://api.openai.com
        """
        self._api_key = api_key
        self._base_url = (base_url or OPENAI_DEFAULT_BASE_URL).rstrip("/")

    @property
    def protocol_name(self) -> str:
        return "openai"

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        thinking: bool,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求（OpenAI Chat Completions API）。

        Args:
            messages: 完整对话上下文（system + user + assistant 交替）
            model: 模型名称，如 "gpt-4o"
            thinking: 是否开启扩展思考（OpenAI 忽略此参数，仅为接口统一保留）

        Yields:
            StreamEvent 序列
        """
        # 构建 OpenAI Chat Completions 请求体
        body = self._build_request_body(messages, model)
        url = f"{self._base_url}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            # 发起流式 POST 请求
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as response:
                    # 检查 HTTP 状态码：非 2xx 视为错误
                    if response.status_code != 200:
                        error_text = await response.aread()
                        error_msg = self._parse_error(response.status_code, error_text)
                        yield StreamEvent(
                            kind=StreamEvent.KIND_ERROR, error=error_msg
                        )
                        return

                    # 逐行读取 SSE 流
                    async for line in response.aiter_lines():
                        # OpenAI SSE 每行以 "data: " 开头
                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]  # 去掉 "data: " 前缀

                        # [DONE] 标记：流正常结束
                        if data_str.strip() == "[DONE]":
                            yield StreamEvent(kind=StreamEvent.KIND_DONE)
                            return

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            # 无法解析的行跳过（理论上不应出现）
                            continue

                        # 从 choices[0].delta.content 提取文本增量
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield StreamEvent(
                                    kind=StreamEvent.KIND_TEXT_DELTA, text=content
                                )

                    # 流结束但未收到 [DONE]：异常情况
                    yield StreamEvent(kind=StreamEvent.KIND_ERROR, error="连接意外关闭")

        except httpx.HTTPError as e:
            # 网络层错误（连接失败、超时等）
            yield StreamEvent(
                kind=StreamEvent.KIND_ERROR,
                error=f"网络请求失败：{e}",
            )

    def _build_request_body(
        self, messages: list[ChatMessage], model: str
    ) -> dict:
        """将内部消息列表转换为 OpenAI Chat Completions 请求体。

        OpenAI 的 messages 数组直接包含所有消息，
        system 提示以 role="system" 的条目存在于 messages 中。

        Args:
            messages: 完整对话上下文
            model: 模型标识

        Returns:
            OpenAI API 请求体字典
        """
        openai_messages = []
        for msg in messages:
            openai_messages.append({"role": msg.role, "content": msg.content})

        return {
            "model": model,
            "messages": openai_messages,
            "stream": True,
        }

    def _parse_error(self, status_code: int, body_bytes: bytes) -> str:
        """解析 HTTP 错误响应，提取可读的错误消息。

        Args:
            status_code: HTTP 状态码
            body_bytes: 响应体原始字节

        Returns:
            面向用户的错误描述
        """
        try:
            body = json.loads(body_bytes)
            error_data = body.get("error", {})
            api_message = error_data.get("message", "")
            error_type = error_data.get("type", "")
            if api_message:
                return f"OpenAI API 错误 ({status_code} {error_type})：{api_message}"
        except (json.JSONDecodeError, AttributeError):
            pass

        # 降级：使用 HTTP 状态码和原始响应
        text = body_bytes.decode("utf-8", errors="replace")[:200]
        if status_code == 401:
            return "鉴权失败（401）：API 密钥无效或已过期"
        elif status_code == 429:
            return "请求过于频繁（429）：API 限流，请稍候再试"
        elif status_code == 404:
            return "端点未找到（404）：请检查模型名称或自定义 base_url"
        elif status_code >= 500:
            return f"服务器错误（{status_code}）：远程服务暂时不可用，请稍后重试"
        return f"HTTP 错误 {status_code}：{text}"

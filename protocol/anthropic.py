"""Anthropic 协议适配器

实现 Anthropic Messages API 的流式对话。
参考：https://docs.anthropic.com/en/api/messages

SSE 事件流格式：
- event: message_start    → data: {message: {id, model, ...}}
- event: content_block_start → data: {index, content_block: {type, ...}}
- event: content_block_delta  → data: {index, delta: {type, text/thinking, ...}}
- event: content_block_stop   → data: {index}
- event: message_delta        → data: {delta: {stop_reason, ...}}
- event: message_stop         → data: {}
"""

import json
from typing import AsyncIterator

import httpx

from protocol.models import ChatMessage, ChatProtocol, StreamEvent


# Anthropic Messages API 默认端点
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
# API 版本头（Anthropic 要求）
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProtocol(ChatProtocol):
    """Anthropic Messages API 协议适配器。

    负责将内部的 ChatMessage 列表转换为 Anthropic 请求格式、
    发起流式 HTTP 请求、并解析 SSE 响应为统一的 StreamEvent 序列。

    关键处理：
    - system prompt 从 messages 中提取为独立的 system 参数
    - thinking 参数映射为 Anthropic 的 thinking budget
    - thinking_delta 事件被识别并标记，由引擎层决定是否丢弃
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        """初始化 Anthropic 协议适配器。

        Args:
            api_key: Anthropic API 密钥（格式通常为 sk-ant-...）
            base_url: 自定义 API 端点地址，None 则使用默认 https://api.anthropic.com
        """
        self._api_key = api_key
        # 删除末尾斜杠防止双斜杠
        self._base_url = (base_url or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
        # 跟踪正在构建的 tool_use 块（每次请求独立）
        # 键为 content_block index → {id, name, args_str}
        self._pending_tool_uses: dict[int, dict] = {}

    @property
    def protocol_name(self) -> str:
        return "anthropic"

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        thinking: bool,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发起一次流式对话请求（Anthropic Messages API）。

        Args:
            messages: 完整对话上下文（system + user + assistant 交替）
            model: 模型名称，如 "claude-sonnet-4-6"
            thinking: 是否开启扩展思考

        Yields:
            StreamEvent 序列
        """
        # 清除上一次请求可能残留的工具状态
        self._pending_tool_uses.clear()

        # 构建请求体：Anthropic 需要分离 system 和 messages
        body = self._build_request_body(messages, model, thinking, tools)
        url = f"{self._base_url}/v1/messages"

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
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

                    # 逐行读取 SSE 流，按 event/data 行协议解析
                    current_event = None
                    async for line in response.aiter_lines():
                        # 空行表示一个 SSE 事件的结束
                        if not line.strip():
                            if current_event:
                                event = self._parse_sse_event(current_event)
                                if event:
                                    yield event
                                    # done 或 error 事件后停止遍历
                                    if event.kind in (
                                        StreamEvent.KIND_DONE,
                                        StreamEvent.KIND_ERROR,
                                    ):
                                        return
                                current_event = None
                            continue

                        # SSE 行格式："event: <type>" 或 "data: <json>"
                        if line.startswith("event: "):
                            current_event = current_event or {}
                            current_event["event"] = line[7:].strip()
                        elif line.startswith("data: "):
                            current_event = current_event or {}
                            current_event["data"] = line[6:].strip()

                    # 流结束但未收到 message_stop：异常情况
                    yield StreamEvent(kind=StreamEvent.KIND_ERROR, error="连接意外关闭")

        except httpx.HTTPError as e:
            # 网络层错误（连接失败、超时等）
            yield StreamEvent(
                kind=StreamEvent.KIND_ERROR,
                error=f"网络请求失败：{e}",
            )

    def _build_request_body(
        self, messages: list[ChatMessage], model: str, thinking: bool,
        tools: list[dict] | None = None,
    ) -> dict:
        """将内部消息列表转换为 Anthropic Messages API 请求体。

        处理消息格式转换：
        - system → 提取为顶层 "system" 参数
        - user → role="user", content="..."
        - assistant（纯文本）→ role="assistant", content=[{"type": "text", "text": "..."}]
        - assistant（工具调用）→ role="assistant", content=[{"type": "tool_use", ...}]
        - tool → role="user", content=[{"type": "tool_result", ...}]

        Args:
            messages: 完整对话上下文
            model: 模型标识
            thinking: 是否开启扩展思考
            tools: 可选工具定义列表（需转换为 Anthropic 格式）

        Returns:
            Anthropic API 请求体字典
        """
        # 从 messages 中提取 system 消息（Anthropic 要求 system 作为顶层参数，不放在 messages 里）
        system_content = ""
        chat_messages = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.role == "system":
                system_content = msg.content
                i += 1
            elif msg.role == "user":
                chat_messages.append({"role": "user", "content": msg.content})
                i += 1
            elif msg.role == "assistant":
                # assistant 消息可能有工具调用
                if msg.tool_calls:
                    # Anthropic 格式：content 数组中混合 text 和 tool_use 块
                    content_blocks = []
                    if msg.content:
                        content_blocks.append({
                            "type": "text",
                            "text": msg.content,
                        })
                    for tc in msg.tool_calls:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                    chat_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                else:
                    chat_messages.append({
                        "role": "assistant",
                        "content": msg.content,
                    })
                i += 1
            elif msg.role == "tool":
                # 工具结果回灌：Anthropic 要求所有 tool_result 块
                # 合并到一条 role="user" 消息中（而非每条工具结果各发一条）
                # 因此需要收集连续的所有 tool 消息，聚合为一个 user 消息
                tool_result_blocks: list[dict] = []
                while i < len(messages) and messages[i].role == "tool":
                    tmsg = messages[i]
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tmsg.tool_call_id or "",
                        "content": tmsg.content,
                    })
                    i += 1
                chat_messages.append({
                    "role": "user",
                    "content": tool_result_blocks,
                })
            else:
                # 未知角色：跳过避免请求失败
                i += 1

        body: dict = {
            "model": model,
            "messages": chat_messages,
            "stream": True,
            "max_tokens": 8192,
        }

        # 如果有 system prompt，作为顶层参数
        if system_content:
            body["system"] = system_content

        # thinking 参数：设置 thinking budget（至少 1024 tokens）并告知需要思考内容
        if thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": 4096,
            }
            # 注意：开启 thinking 后，响应中会有 thinking_delta 事件

        # 注入工具定义（转换为 Anthropic 格式）
        if tools:
            anthropic_tools = []
            for t in tools:
                func = t.get("function", t)
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                })
            body["tools"] = anthropic_tools

        return body

    def _parse_sse_event(self, event: dict) -> StreamEvent | None:
        """解析单个 SSE 事件，转换为 StreamEvent。

        处理的事件类型：
        - content_block_start: 检测 tool_use 块的开始
        - content_block_delta: 正文增量 / 思考增量 / tool_use 参数增量
        - content_block_stop: tool_use 块结束（产出 tool_call_end）
        - message_stop: 正常结束
        - error: 服务端错误

        Args:
            event: 包含 "event" (事件类型) 和 "data" (JSON 字符串) 的字典

        Returns:
            对应的 StreamEvent，若是无关事件返回 None
        """
        event_type = event.get("event", "")
        data_str = event.get("data", "{}")

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        # ---- content_block_start：检测 tool_use 块的开始 ----
        if event_type == "content_block_start":
            content_block = data.get("content_block", {})
            if content_block.get("type") == "tool_use":
                index = data.get("index", 0)
                tool_id = content_block.get("id", "")
                tool_name = content_block.get("name", "")
                self._pending_tool_uses[index] = {
                    "id": tool_id,
                    "name": tool_name,
                    "args_str": "",
                }
                return StreamEvent(
                    kind=StreamEvent.KIND_TOOL_CALL_START,
                    tool_call_id=tool_id,
                    tool_call_name=tool_name,
                )

        # ---- content_block_delta：正文 / 思考 / tool_use 参数增量 ----
        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "text_delta":
                # 正文增量：提取文本并产出 text_delta 事件
                text = delta.get("text", "")
                return StreamEvent(
                    kind=StreamEvent.KIND_TEXT_DELTA, text=text
                )
            elif delta_type == "thinking_delta":
                # 思考增量：标记为 thinking_delta（引擎层应丢弃）
                return StreamEvent(kind=StreamEvent.KIND_THINKING_DELTA)
            elif delta_type == "input_json_delta":
                # tool_use 的 JSON 参数片段
                index = data.get("index", 0)
                partial_json = delta.get("partial_json", "")
                if index in self._pending_tool_uses:
                    self._pending_tool_uses[index]["args_str"] += partial_json
                    return StreamEvent(
                        kind=StreamEvent.KIND_TOOL_CALL_DELTA,
                        text=partial_json,
                        tool_call_id=self._pending_tool_uses[index]["id"],
                        tool_call_name=self._pending_tool_uses[index]["name"],
                    )

        # ---- content_block_stop：工具调用参数接收完毕 ----
        elif event_type == "content_block_stop":
            index = data.get("index", 0)
            if index in self._pending_tool_uses:
                state = self._pending_tool_uses.pop(index)
                # 解析累积的 JSON 参数
                try:
                    args = json.loads(state["args_str"])
                except json.JSONDecodeError:
                    args = {}
                return StreamEvent(
                    kind=StreamEvent.KIND_TOOL_CALL_END,
                    tool_call_id=state["id"],
                    tool_call_name=state["name"],
                    tool_arguments=args,
                )

        # ---- message_stop：正常结束 ----
        elif event_type == "message_stop":
            return StreamEvent(kind=StreamEvent.KIND_DONE)

        # ---- error：服务端返回的错误事件 ----
        elif event_type == "error":
            error_data = data.get("error", {})
            error_msg = error_data.get("message", str(data))
            return StreamEvent(
                kind=StreamEvent.KIND_ERROR, error=error_msg
            )

        # 其他事件（message_start, ping 等）忽略
        return None

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
            # 确保响应体是 JSON 对象（dict），不是数组等其他类型
            if isinstance(body, dict):
                error_data = body.get("error", {})
                api_message = error_data.get("message", "")
                error_type = error_data.get("type", "")
                if api_message:
                    return f"Anthropic API 错误 ({status_code} {error_type})：{api_message}"
        except json.JSONDecodeError:
            # 响应体不是合法的 JSON，降级到 HTTP 状态码消息
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

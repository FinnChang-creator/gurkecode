"""gurkecode — 多协议 LLM 终端对话客户端

入口模块，负责：
1. 加载服务商配置（YAML + 环境变量）
2. 启动终端 UI（textual App）
   - 若仅一个 provider：直接进入对话
   - 若多个 provider：UI 内弹出选择界面

用法：
    python main.py

依赖配置文件 gurkecode.yaml 存在于项目根目录。
"""

from config.loader import load_providers
from ui.app import GurkeApp


def main() -> None:
    """程序主入口。

    执行流程：
    1. 加载配置 → 2. 启动 UI（provider 选择内置于 UI 中）
    """
    # ---- 1. 加载服务商配置 ----
    # 从 gurkecode.yaml 读取 + 检测环境变量中的 Anthropic 密钥
    providers = load_providers("gurkecode.yaml")

    # ---- 2. 启动终端 UI ----
    # GurkeApp 内部处理单/多 provider 的选择逻辑
    app = GurkeApp(providers=providers)
    app.run()


if __name__ == "__main__":
    main()

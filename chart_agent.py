"""在 ACA Sandbox 中生成图表的 Microsoft Agent Framework console agent。"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient
from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

load_dotenv()

REMOTE_CHART_PATH = "/tmp/plot.png"
CHART_INSTRUCTIONS = """
你是图表 Python 代码生成器。根据用户提供的数据与图表要求，返回且只返回可直接执行的
Python 代码，不要使用 Markdown 代码围栏，也不要提供解释。

代码必须：
1. import matplotlib.pyplot as plt 和 import seaborn as sns；
2. 定义 file_name = "plot.png"；
3. 设置 plt.rcParams["font.sans-serif"] = ["SimHei"] 和
   plt.rcParams["axes.unicode_minus"] = False；
4. 在 try/except 中绘制图表，包含标题、坐标轴标签以及必要的数据标签；
5. 调用 plt.savefig(file_name, bbox_inches="tight")；
6. try 块最后一行 print(file_name)，except 中打印以 "Error:" 开头的错误。

若用户未指定图表类型，请按数据特征选择适合的图表。保留用户给出的字段名原文。
"""


def required_env(name: str) -> str:
    """读取必须配置的环境变量，并给出可操作的错误。"""
    value = os.getenv(name, "").strip()
    if not value or value.startswith("YOUR_"):
        raise RuntimeError(f"请在 .env 中配置 {name}。可从 .env.example 开始。")
    return value


def create_chat_client() -> OpenAIChatCompletionClient:
    """创建 Azure OpenAI 客户端；本机开发默认使用 API Key，也支持 az login。"""
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip() or None
    credential = None if api_key else AzureCliCredential()
    return OpenAIChatCompletionClient(
        model=required_env("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
        api_key=api_key,
        credential=credential,
        azure_endpoint=required_env("AZURE_OPENAI_ENDPOINT"),
        api_version=os.getenv("OPENAI_API_VERSION", "2025-03-01-preview"),
    )


def create_sandbox_client() -> SandboxGroupClient:
    """使用当前 az login 身份连接到指定 ACA Sandbox Group。"""
    return SandboxGroupClient(
        endpoint_for_region(required_env("ACA_SANDBOX_REGION")),
        AzureCliCredential(),
        subscription_id=required_env("AZURE_SUBSCRIPTION_ID"),
        resource_group=required_env("ACA_SANDBOX_RESOURCE_GROUP"),
        sandbox_group=required_env("ACA_SANDBOX_GROUP"),
    )


def sandbox_options() -> dict[str, str]:
    """优先使用由 chart 自定义镜像创建的 Disk，防止落到不含字体和 Seaborn 的基础镜像。"""
    disk_id = os.getenv("CHART_SANDBOX_DISK_ID", "").strip()
    if disk_id and not disk_id.startswith("YOUR_"):
        return {"disk_id": disk_id}
    disk = os.getenv("CHART_SANDBOX_DISK", "").strip()
    if disk:
        return {"disk": disk}
    raise RuntimeError(
        "请配置 CHART_SANDBOX_DISK_ID。该 Disk 必须由 sandboxes/chart 的自定义镜像创建。"
    )


def execute_chart_in_sandbox(code: str) -> Path:
    """把模型生成的代码放入短生命周期 Sandbox 执行，并仅将 PNG 下载到本地。"""
    output_dir = Path(os.getenv("CHART_OUTPUT_DIR", "output/charts"))
    output_dir.mkdir(parents=True, exist_ok=True)
    local_chart_path = output_dir / f"chart-{uuid.uuid4().hex}.png"
    remote_code_path = f"/tmp/chart-{uuid.uuid4().hex}.py"
    remote_code = code.replace("plot.png", REMOTE_CHART_PATH)
    group_client: SandboxGroupClient | None = None
    sandbox = None

    try:
        group_client = create_sandbox_client()

        # 1. 使用预置 Seaborn/SimHei 的自定义 Disk 创建隔离执行环境。
        #    模型生成的 Python 绝不在运行 console 的电脑上执行。
        sandbox = group_client.begin_create_sandbox(**sandbox_options()).result()

        # 2. 通过 Sandbox 文件 API 上传随机命名脚本，避免并发运行时互相覆盖。
        sandbox.write_file(remote_code_path, remote_code)

        # 3. 仅在隔离容器内调用 Python，标准输出必须声明约定的图表文件位置。
        result = sandbox.exec(f"python3 {remote_code_path}")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "Sandbox 执行失败").strip()
            raise RuntimeError(f"Sandbox 执行失败: {detail}")
        output = (result.stdout or "").strip().splitlines()
        if not output or output[-1] != REMOTE_CHART_PATH:
            raise RuntimeError(
                "图表代码没有输出预期文件路径 /tmp/plot.png。"
                f"实际输出: {(result.stdout or '').strip()}"
            )

        # 4. 成功后只读回 PNG 字节并保存到本机 output/charts；用户自行打开查看。
        content = sandbox.read_file(REMOTE_CHART_PATH)
        if not content:
            raise RuntimeError("Sandbox 返回了空的图表文件。")
        local_chart_path.write_bytes(content)
        return local_chart_path.resolve()
    except Exception:
        local_chart_path.unlink(missing_ok=True)
        raise
    finally:
        # 5. 无论成功、失败或 Ctrl+C，均删除短生命周期 Sandbox，避免遗留计费资源。
        if sandbox is not None:
            try:
                sandbox.delete()
            except Exception as cleanup_error:  # noqa: BLE001
                print(f"警告：未能删除 Sandbox: {cleanup_error}")
        if group_client is not None:
            group_client.close()


async def generate_chart(agent: Agent, request: str) -> Path:
    """生成并执行图表代码；第一次 Sandbox 失败时让模型按错误反馈修复一次。"""
    code_response = await agent.run(request)
    code = code_response.text
    try:
        return execute_chart_in_sandbox(code)
    except RuntimeError as first_error:
        repair_prompt = (
            "上一次生成的图表代码在 Sandbox 中执行失败。请仅返回修复后的完整 Python 代码。\n"
            f"错误：{first_error}"
        )
        repaired_response = await agent.run(repair_prompt)
        return execute_chart_in_sandbox(repaired_response.text)


async def main() -> None:
    agent = Agent(
        name="chart-agent",
        description="根据用户数据生成本地 PNG 图表。",
        client=create_chat_client(),
        instructions=CHART_INSTRUCTIONS,
    )
    print("Chart Agent 已启动。输入图表需求；输入 exit 退出。")
    while True:
        request = input("\n图表需求> ").strip()
        if request.lower() in {"exit", "quit", "退出"}:
            return
        if not request:
            continue
        try:
            chart_path = await generate_chart(agent, request)
            print(f"图表已保存：{chart_path}")
        except Exception as error:  # noqa: BLE001
            print(f"生成失败：{error}")


if __name__ == "__main__":
    asyncio.run(main())
"""General Q&A console agent with one bounded ACA scheduling tool."""

from __future__ import annotations

import asyncio

from agent_framework import Agent

from scheduled_summary_job import create_scheduled_website_summary
from sum_site_job import create_chat_client

ASK_INSTRUCTIONS = """
你是通用问答助手。普通问题直接回答，不得调用工具。

仅当用户明确要求“每天”定时总结网站时，才可调用 create_scheduled_website_summary。
调用前必须从用户处获得：明确的网站 URL、每日执行小时和分钟。用户未指定时区时省略 time_zone，
由工具使用配置的默认时区。若 URL、小时或分钟缺失，先要求用户补充，绝不调用工具。
不要猜测相对时间、模糊时间或非每日周期；说明当前只支持明确的每日小时和分钟。
工具成功后，用简洁文字告知 Job 名称、规范化 URL、本地时区和 UTC cron。
工具报错时仅解释工具返回的安全错误，不索取或显示凭据。
"""


def create_ask_agent(client=None, scheduling_tool=create_scheduled_website_summary) -> Agent:
    return Agent(
        name="ask-agent",
        description="回答普通问题，并按明确请求创建或更新每日网站摘要任务。",
        client=client or create_chat_client(),
        instructions=ASK_INSTRUCTIONS,
        tools=[scheduling_tool],
    )


async def run_request(agent: Agent, request: str) -> str:
    response = await agent.run(request)
    return response.text


async def main() -> None:
    agent = create_ask_agent()
    print("Ask Agent 已启动。可直接提问或创建每日网站摘要任务；输入 exit 退出。")
    while True:
        request = input("\n问题> ").strip()
        if request.lower() in {"exit", "quit", "退出"}:
            return
        if not request:
            continue
        try:
            print(f"\n{await run_request(agent, request)}")
        except Exception as error:  # noqa: BLE001
            print(f"处理失败：{error}")


if __name__ == "__main__":
    asyncio.run(main())
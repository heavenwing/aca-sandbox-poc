import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import ask_agent
import sum_site_job


def test_ordinary_question_returns_answer_without_tool_call() -> None:
    tool = MagicMock()
    agent = MagicMock()
    agent.run = AsyncMock(return_value=SimpleNamespace(text="Paris"))

    result = asyncio.run(ask_agent.run_request(agent, "法国首都是哪里？"))

    assert result == "Paris"
    tool.assert_not_called()


def test_incomplete_schedule_does_not_call_tool() -> None:
    tool = MagicMock()
    agent = MagicMock()
    agent.run = AsyncMock(return_value=SimpleNamespace(text="请提供网站 URL。"))

    result = asyncio.run(ask_agent.run_request(agent, "每天早上八点总结网站"))

    assert "URL" in result
    tool.assert_not_called()


def test_valid_schedule_calls_bounded_tool_once() -> None:
    tool = MagicMock(return_value={"job_name": "sum-site-example", "utc_cron": "0 0 * * *"})

    class FakeAgent:
        async def run(self, request):
            result = tool("example.com", 8, 0, None)
            return SimpleNamespace(text=f"已配置 {result['job_name']}")

    result = asyncio.run(ask_agent.run_request(FakeAgent(), "每天 8:00 总结 example.com"))

    assert result == "已配置 sum-site-example"
    tool.assert_called_once_with("example.com", 8, 0, None)


def test_ask_agent_registers_only_bounded_tool(monkeypatch) -> None:
    created = {}

    def fake_agent(**kwargs):
        created.update(kwargs)
        return MagicMock()

    tool = MagicMock()
    monkeypatch.setattr(ask_agent, "Agent", fake_agent)

    ask_agent.create_ask_agent(client=MagicMock(), scheduling_tool=tool)

    assert created["tools"] == [tool]
    assert "小时和分钟" in created["instructions"]


def test_sandbox_client_selects_user_assigned_identity(monkeypatch) -> None:
    credential = MagicMock()
    credential_factory = MagicMock(return_value=credential)
    sandbox_client = MagicMock()
    sandbox_client_factory = MagicMock(return_value=sandbox_client)
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("ACA_SANDBOX_REGION", "eastasia")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sandbox-sub")
    monkeypatch.setenv("ACA_SANDBOX_RESOURCE_GROUP", "sandbox-rg")
    monkeypatch.setenv("ACA_SANDBOX_GROUP", "sandbox-group")
    monkeypatch.setattr(sum_site_job, "DefaultAzureCredential", credential_factory)
    monkeypatch.setattr(sum_site_job, "endpoint_for_region", lambda region: f"https://{region}.example.com")
    monkeypatch.setattr(sum_site_job, "SandboxGroupClient", sandbox_client_factory)

    assert sum_site_job.create_sandbox_client() is sandbox_client
    credential_factory.assert_called_once_with(
        managed_identity_client_id="11111111-2222-3333-4444-555555555555"
    )
    sandbox_client_factory.assert_called_once_with(
        "https://eastasia.example.com",
        credential,
        subscription_id="sandbox-sub",
        resource_group="sandbox-rg",
        sandbox_group="sandbox-group",
    )


def test_one_shot_runs_once_without_input(monkeypatch, capsys, tmp_path) -> None:
    agent = MagicMock()
    run_summary = AsyncMock(return_value="网站摘要")
    monkeypatch.setenv("SUM_SITE_URL", "example.com")
    monkeypatch.setenv("SUM_SITE_OUTPUT_PATH", str(tmp_path))
    monkeypatch.setattr(sum_site_job, "create_agent", lambda: agent)
    monkeypatch.setattr(sum_site_job, "run_summary", run_summary)
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=AssertionError("must not read input")))

    exit_code = asyncio.run(sum_site_job.main())

    assert exit_code == 0
    run_summary.assert_awaited_once_with(agent, "请总结 https://example.com/")
    assert capsys.readouterr().out.strip() == "网站摘要"
    output_files = list(tmp_path.glob("https-example-com-*.md"))
    assert len(output_files) == 1
    assert output_files[0].read_text(encoding="utf-8") == "网站摘要\n"


def test_summary_filename_contains_safe_url_and_utc_timestamp() -> None:
    timestamp = datetime(2026, 8, 20, 12, 34, 56, tzinfo=timezone.utc)

    filename = sum_site_job.summary_filename("https://example.com/news?id=42", timestamp)

    assert filename == "https-example-com-news-id-42-20260820T123456Z.md"


def test_one_shot_failure_returns_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SUM_SITE_URL", "example.com")
    monkeypatch.setattr(sum_site_job, "create_agent", MagicMock())
    monkeypatch.setattr(sum_site_job, "run_summary", AsyncMock(side_effect=RuntimeError("fetch failed")))

    assert asyncio.run(sum_site_job.main()) == 1
    assert "fetch failed" in capsys.readouterr().err
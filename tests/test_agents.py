from types import SimpleNamespace
from unittest.mock import MagicMock

import chart_agent
import sum_site_agent


def _configure_sandbox(monkeypatch) -> None:
    monkeypatch.setenv("ACA_SANDBOX_REGION", "eastasia")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
    monkeypatch.setenv("ACA_SANDBOX_RESOURCE_GROUP", "resource-group")
    monkeypatch.setenv("ACA_SANDBOX_GROUP", "sandbox-group")


def test_chart_downloads_png_and_deletes_sandbox(monkeypatch, tmp_path) -> None:
    _configure_sandbox(monkeypatch)
    monkeypatch.setenv("CHART_SANDBOX_DISK_ID", "chart-disk")
    monkeypatch.setenv("CHART_OUTPUT_DIR", str(tmp_path))
    sandbox = MagicMock()
    sandbox.exec.return_value = SimpleNamespace(
        exit_code=0, stdout="/tmp/plot.png\n", stderr=""
    )
    sandbox.read_file.return_value = b"png-content"
    client = MagicMock()
    client.begin_create_sandbox.return_value.result.return_value = sandbox
    monkeypatch.setattr(chart_agent, "create_sandbox_client", lambda: client)

    result = chart_agent.execute_chart_in_sandbox('file_name = "plot.png"')

    assert result.read_bytes() == b"png-content"
    client.begin_create_sandbox.assert_called_once_with(disk_id="chart-disk")
    assert sandbox.write_file.call_args.args[1] == 'file_name = "/tmp/plot.png"'
    sandbox.delete.assert_called_once_with()
    client.close.assert_called_once_with()


def test_sum_site_fetch_uses_fixed_runner_and_cleans_files(monkeypatch) -> None:
    _configure_sandbox(monkeypatch)
    monkeypatch.setenv("SUM_SITE_SANDBOX_DISK_ID", "site-disk")
    sandbox = MagicMock()
    sandbox.exec.return_value = SimpleNamespace(exit_code=0, stdout="", stderr="")
    sandbox.read_file.return_value = (
        b'{"url":"https://example.com/","final_url":"https://example.com/",'
        b'"status_code":200,"title":"Example","content":"Content"}'
    )
    client = MagicMock()
    client.begin_create_sandbox.return_value.result.return_value = sandbox
    monkeypatch.setattr(sum_site_agent, "create_sandbox_client", lambda: client)

    result = sum_site_agent.fetch_website_in_sandbox("example.com")

    assert result.title == "Example"
    client.begin_create_sandbox.assert_called_once_with(disk_id="site-disk")
    command = sandbox.exec.call_args.args[0]
    assert command.startswith("python3 /opt/sum-site/fetch_runner.py /tmp/")
    assert sandbox.delete_file.call_count == 2
    sandbox.delete.assert_called_once_with()
    client.close.assert_called_once_with()


def test_normalize_public_url_rejects_credentials() -> None:
    assert sum_site_agent.normalize_public_url("example.com") == "https://example.com/"
    try:
        sum_site_agent.normalize_public_url("https://user:password@example.com")
    except ValueError as error:
        assert "用户名" in str(error)
    else:
        raise AssertionError("Expected credentials in URL to be rejected")
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import scheduled_summary_job as jobs


def _env() -> dict[str, str]:
    return {
        "ACA_JOB_SUBSCRIPTION_ID": "job-sub",
        "ACA_JOB_RESOURCE_GROUP": "jobs-rg",
        "ACA_JOB_ENVIRONMENT_ID": "/subscriptions/job-sub/resourceGroups/jobs-rg/providers/Microsoft.App/managedEnvironments/env",
        "ACA_JOB_IDENTITY_ID": "/subscriptions/job-sub/resourceGroups/jobs-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/sum-site",
        "ACA_JOB_LOCATION": "eastasia",
        "ACA_JOB_IMAGE": "registry.example.com/sum-site:sha-123",
        "ACA_JOB_REGISTRY_SERVER": "registry.example.com",
        "ACA_JOB_REGISTRY_USERNAME": "registry-user",
        "ACA_JOB_REGISTRY_PASSWORD": "registry-secret-value",
        "AZURE_OPENAI_ENDPOINT": "https://openai.example.com/",
        "AZURE_OPENAI_API_KEY": "openai-secret-value",
        "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": "chat",
        "OPENAI_API_VERSION": "2025-03-01-preview",
        "ACA_SANDBOX_REGION": "eastasia",
        "AZURE_SUBSCRIPTION_ID": "sandbox-sub",
        "ACA_SANDBOX_RESOURCE_GROUP": "sandbox-rg",
        "ACA_SANDBOX_GROUP": "sandbox-group",
        "SUM_SITE_SANDBOX_DISK_ID": "/subscriptions/sandbox-sub/disks/site",
    }


def test_url_cron_and_stable_name() -> None:
    first = jobs.normalize_url("EXAMPLE.com:443/path#fragment")
    second = jobs.normalize_url("https://example.com/path")

    assert first == second == "https://example.com/path"
    assert jobs.utc_cron(8, 0, "Asia/Shanghai") == "0 0 * * *"
    assert jobs.job_name(first) == jobs.job_name(second)
    assert jobs.JOB_NAME_PATTERN.fullmatch(jobs.job_name(first))


def test_rejects_credentials_invalid_time_and_dst() -> None:
    with pytest.raises(ValueError, match="用户名"):
        jobs.normalize_url("https://user:password@example.com")
    with pytest.raises(ValueError, match="0 到 23"):
        jobs.utc_cron(24, 0, "Asia/Shanghai")
    with pytest.raises(ValueError, match="UTC 偏移"):
        jobs.utc_cron(8, 0, "America/New_York")


def test_build_payload_uses_secret_refs_and_single_replica() -> None:
    config = jobs.load_config(_env())
    request = jobs.ScheduledSummaryRequest("https://example.com/", 8, 0, "Asia/Shanghai")

    payload = jobs.build_payload(config, request)
    configuration = payload["properties"]["configuration"]
    container = payload["properties"]["template"]["containers"][0]

    assert configuration["scheduleTriggerConfig"] == {
        "cronExpression": "0 0 * * *",
        "parallelism": 1,
        "replicaCompletionCount": 1,
    }
    assert payload["identity"]["type"] == "UserAssigned"
    assert config.identity_id in payload["identity"]["userAssignedIdentities"]
    assert configuration["registries"][0]["passwordSecretRef"] == "registry-password"
    assert {item["name"]: item for item in container["env"]}["AZURE_OPENAI_API_KEY"] == {
        "name": "AZURE_OPENAI_API_KEY",
        "secretRef": "azure-openai-api-key",
    }
    assert {item["name"]: item for item in container["env"]}["SUM_SITE_URL"]["value"] == request.url
    assert "registry-secret-value" not in json.dumps(container)
    assert "openai-secret-value" not in json.dumps(container)


def test_create_or_update_puts_once_and_returns_no_secrets() -> None:
    credential = MagicMock()
    credential.get_token.return_value = SimpleNamespace(token="arm-secret-token")
    transport = MagicMock(return_value=(201, b'{"id":"/jobs/name","properties":{"provisioningState":"Succeeded"}}'))

    result = jobs.create_or_update_job(
        "example.com", 8, 0, env=_env(), credential_factory=lambda: credential, transport=transport
    )

    assert result["utc_cron"] == "0 0 * * *"
    assert result["resource_id"] == "/jobs/name"
    assert "secret" not in json.dumps(result)
    method, uri, headers, body = transport.call_args.args
    assert method == "PUT"
    assert "/providers/Microsoft.App/jobs/sum-site-" in uri
    assert uri.endswith("?api-version=2025-07-01")
    assert headers["Authorization"] == "Bearer arm-secret-token"
    assert json.loads(body)["properties"]["configuration"]["triggerType"] == "Schedule"


def test_invalid_config_fails_before_credential_creation() -> None:
    env = _env()
    env.pop("ACA_JOB_IMAGE")
    credential_factory = MagicMock()

    with pytest.raises(ValueError, match="ACA_JOB_IMAGE"):
        jobs.create_or_update_job("example.com", 8, 0, env=env, credential_factory=credential_factory)

    credential_factory.assert_not_called()


def test_arm_error_is_reduced_to_status_code_and_message() -> None:
    credential = MagicMock()
    credential.get_token.return_value = SimpleNamespace(token="arm-secret-token")
    transport = MagicMock(return_value=(403, b'{"error":{"code":"AuthorizationFailed","message":"Denied openai-secret-value arm-secret-token"}}'))

    with pytest.raises(RuntimeError, match="ARM 403 AuthorizationFailed: Denied") as caught:
        jobs.create_or_update_job(
            "example.com", 8, 0, env=_env(), credential_factory=lambda: credential, transport=transport
        )

    assert "arm-secret-token" not in str(caught.value)
    assert "openai-secret-value" not in str(caught.value)
    assert str(caught.value).count("[REDACTED]") == 2
"""Create or update one application-owned ACA scheduled website-summary job."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from azure.identity import AzureCliCredential

ARM_SCOPE = "https://management.azure.com/.default"
DEFAULT_API_VERSION = "2025-07-01"
SECRET_ENV_NAMES = {"AZURE_OPENAI_API_KEY": "azure-openai-api-key"}
JOB_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")


@dataclass(frozen=True)
class ScheduledSummaryRequest:
    url: str
    hour: int
    minute: int
    time_zone: str


@dataclass(frozen=True)
class JobConfig:
    subscription_id: str
    resource_group: str
    environment_id: str
    identity_id: str
    location: str
    image: str
    registry_server: str
    registry_username: str
    registry_password: str
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment: str
    openai_api_version: str
    sandbox_region: str
    sandbox_subscription_id: str
    sandbox_resource_group: str
    sandbox_group: str
    sandbox_disk_id: str
    output_storage_name: str
    output_storage_type: str = "AzureFile"
    output_path: str = "/mnt/output"
    name_prefix: str = "sum-site"
    api_version: str = DEFAULT_API_VERSION
    cpu: str = "0.5"
    memory: str = "1Gi"
    replica_timeout: int = 1800
    replica_retry_limit: int = 0


Transport = Callable[[str, str, Mapping[str, str], bytes], tuple[int, bytes]]


def _required(env: Mapping[str, str], name: str, fallback: str | None = None) -> str:
    value = env.get(name, "").strip()
    if not value and fallback:
        value = env.get(fallback, "").strip()
    if not value or value.startswith("YOUR_"):
        raise ValueError(f"必须配置 {name}。")
    return value


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数。") from error
    if value < minimum:
        raise ValueError(f"{name} 必须大于或等于 {minimum}。")
    return value


def normalize_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("必须提供网站 URL。")
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅允许绝对 HTTP(S) URL。")
    if parsed.username or parsed.password:
        raise ValueError("URL 不得包含用户名或密码。")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL 端口无效。") from error
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if "." not in host and host != "localhost":
        raise ValueError("URL 必须包含有效的主机名。")
    if host == "localhost":
        raise ValueError("URL 必须指向公开网站。")
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def utc_cron(hour: int, minute: int, time_zone: str) -> str:
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("小时必须是 0 到 23 的整数。")
    if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("分钟必须是 0 到 59 的整数。")
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"无效的 IANA 时区: {time_zone}") from error

    offsets = set()
    current = datetime(2024, 1, 1, hour, minute, tzinfo=zone)
    end = datetime(2029, 1, 1, hour, minute, tzinfo=zone)
    while current < end:
        offsets.add(current.utcoffset())
        current += timedelta(days=1)
    if len(offsets) != 1 or None in offsets:
        raise ValueError("该时区的 UTC 偏移会变化，无法使用单个固定 UTC cron。")
    offset = offsets.pop()
    assert offset is not None
    utc_minutes = (hour * 60 + minute - int(offset.total_seconds() // 60)) % (24 * 60)
    return f"{utc_minutes % 60} {utc_minutes // 60} * * *"


def job_name(normalized_url: str, prefix: str = "sum-site") -> str:
    clean_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.lower()).strip("-")
    if not clean_prefix or not clean_prefix[0].isalpha():
        raise ValueError("ACA_JOB_NAME_PREFIX 必须以字母开头。")
    host = urlsplit(normalized_url).hostname or "site"
    host_slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "site"
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:10]
    available = 32 - len(clean_prefix) - len(digest) - 2
    if available < 1:
        raise ValueError("ACA_JOB_NAME_PREFIX 过长。")
    name = f"{clean_prefix}-{host_slug[:available].rstrip('-')}-{digest}"
    if not JOB_NAME_PATTERN.fullmatch(name):
        raise ValueError("生成的 Job 名称不符合 ACA 命名约束。")
    return name


def load_config(env: Mapping[str, str] | None = None) -> JobConfig:
    values = os.environ if env is None else env
    config = JobConfig(
        subscription_id=_required(values, "ACA_JOB_SUBSCRIPTION_ID", "AZURE_SUBSCRIPTION_ID"),
        resource_group=_required(values, "ACA_JOB_RESOURCE_GROUP"),
        environment_id=_required(values, "ACA_JOB_ENVIRONMENT_ID"),
        identity_id=_required(values, "ACA_JOB_IDENTITY_ID"),
        location=_required(values, "ACA_JOB_LOCATION"),
        image=_required(values, "ACA_JOB_IMAGE"),
        registry_server=_required(values, "ACA_JOB_REGISTRY_SERVER"),
        registry_username=_required(values, "ACA_JOB_REGISTRY_USERNAME"),
        registry_password=_required(values, "ACA_JOB_REGISTRY_PASSWORD"),
        azure_openai_endpoint=_required(values, "AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_key=_required(values, "AZURE_OPENAI_API_KEY"),
        azure_openai_deployment=_required(values, "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
        openai_api_version=values.get("OPENAI_API_VERSION", "2025-03-01-preview").strip(),
        sandbox_region=_required(values, "ACA_SANDBOX_REGION"),
        sandbox_subscription_id=_required(values, "AZURE_SUBSCRIPTION_ID"),
        sandbox_resource_group=_required(values, "ACA_SANDBOX_RESOURCE_GROUP"),
        sandbox_group=_required(values, "ACA_SANDBOX_GROUP"),
        sandbox_disk_id=_required(values, "SUM_SITE_SANDBOX_DISK_ID"),
        output_storage_name=_required(values, "ACA_JOB_OUTPUT_STORAGE_NAME"),
        output_storage_type=values.get("ACA_JOB_OUTPUT_STORAGE_TYPE", "AzureFile").strip(),
        output_path=values.get("SUM_SITE_OUTPUT_PATH", "/mnt/output").strip() or "/mnt/output",
        name_prefix=values.get("ACA_JOB_NAME_PREFIX", "sum-site").strip(),
        api_version=values.get("ACA_JOB_API_VERSION", DEFAULT_API_VERSION).strip(),
        cpu=values.get("ACA_JOB_CPU", "0.5").strip(),
        memory=values.get("ACA_JOB_MEMORY", "1Gi").strip(),
        replica_timeout=_integer(values, "ACA_JOB_REPLICA_TIMEOUT", 1800, 1),
        replica_retry_limit=_integer(values, "ACA_JOB_REPLICA_RETRY_LIMIT", 0, 0),
    )
    if not config.environment_id.startswith("/subscriptions/"):
        raise ValueError("ACA_JOB_ENVIRONMENT_ID 必须是完整 ARM 资源 ID。")
    if not config.identity_id.startswith("/subscriptions/"):
        raise ValueError("ACA_JOB_IDENTITY_ID 必须是完整 ARM 资源 ID。")
    if not re.fullmatch(r"[a-z0-9.-]+", config.registry_server.lower()):
        raise ValueError("ACA_JOB_REGISTRY_SERVER 无效。")
    if not config.image.startswith(f"{config.registry_server}/"):
        raise ValueError("ACA_JOB_IMAGE 必须来自配置的 registry server。")
    if not re.fullmatch(r"\d+(?:\.\d+)?", config.cpu):
        raise ValueError("ACA_JOB_CPU 无效。")
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:Mi|Gi)", config.memory):
        raise ValueError("ACA_JOB_MEMORY 无效。")
    if config.output_storage_type not in {"AzureFile", "NfsAzureFile"}:
        raise ValueError("ACA_JOB_OUTPUT_STORAGE_TYPE 必须是 AzureFile 或 NfsAzureFile。")
    if not config.output_path.startswith("/"):
        raise ValueError("SUM_SITE_OUTPUT_PATH 必须是容器内的绝对路径。")
    return config


def build_payload(config: JobConfig, request: ScheduledSummaryRequest) -> dict[str, Any]:
    non_secret_env = {
        "SUM_SITE_URL": request.url,
        "AZURE_OPENAI_ENDPOINT": config.azure_openai_endpoint,
        "AZURE_OPENAI_CHAT_DEPLOYMENT_NAME": config.azure_openai_deployment,
        "OPENAI_API_VERSION": config.openai_api_version,
        "ACA_SANDBOX_REGION": config.sandbox_region,
        "AZURE_SUBSCRIPTION_ID": config.sandbox_subscription_id,
        "ACA_SANDBOX_RESOURCE_GROUP": config.sandbox_resource_group,
        "ACA_SANDBOX_GROUP": config.sandbox_group,
        "SUM_SITE_SANDBOX_DISK_ID": config.sandbox_disk_id,
        "SUM_SITE_OUTPUT_PATH": config.output_path,
    }
    environment = [{"name": name, "value": value} for name, value in non_secret_env.items()]
    environment.append({"name": "AZURE_OPENAI_API_KEY", "secretRef": "azure-openai-api-key"})
    return {
        "location": config.location,
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {config.identity_id: {}},
        },
        "properties": {
            "environmentId": config.environment_id,
            "configuration": {
                "triggerType": "Schedule",
                "scheduleTriggerConfig": {
                    "cronExpression": utc_cron(request.hour, request.minute, request.time_zone),
                    "parallelism": 1,
                    "replicaCompletionCount": 1,
                },
                "replicaRetryLimit": config.replica_retry_limit,
                "replicaTimeout": config.replica_timeout,
                "secrets": [
                    {"name": "registry-password", "value": config.registry_password},
                    {"name": "azure-openai-api-key", "value": config.azure_openai_api_key},
                ],
                "registries": [{
                    "server": config.registry_server,
                    "username": config.registry_username,
                    "passwordSecretRef": "registry-password",
                }],
            },
            "template": {
                "containers": [{
                    "name": "sum-site",
                    "image": config.image,
                    "env": environment,
                    "resources": {"cpu": config.cpu, "memory": config.memory},
                    "volumeMounts": [{
                        "volumeName": "summary-output",
                        "mountPath": config.output_path,
                    }],
                }],
                "volumes": [{
                    "name": "summary-output",
                    "storageType": config.output_storage_type,
                    "storageName": config.output_storage_name,
                }],
            },
        },
    }


def build_uri(config: JobConfig, name: str) -> str:
    subscription = quote(config.subscription_id, safe="")
    resource_group = quote(config.resource_group, safe="")
    return (
        f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/"
        f"{resource_group}/providers/Microsoft.App/jobs/{name}"
        f"?api-version={quote(config.api_version, safe='') }"
    )


def _http_transport(method: str, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except URLError as error:
        raise RuntimeError(f"ARM 请求失败: {error.reason}") from error


def _azure_error(status: int, body: bytes, sensitive_values: tuple[str, ...]) -> RuntimeError:
    code = "ArmRequestFailed"
    message = "Azure Resource Manager 请求失败。"
    try:
        error = json.loads(body).get("error", {})
        code = str(error.get("code") or code)
        message = str(error.get("message") or message)
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        pass
    message = message[:1000]
    for value in sensitive_values:
        if value:
            message = message.replace(value, "[REDACTED]")
    return RuntimeError(f"ARM {status} {code}: {message}")


def create_or_update_job(
    url: str,
    hour: int,
    minute: int,
    time_zone: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    credential_factory: Callable[[], Any] = AzureCliCredential,
    transport: Transport = _http_transport,
) -> dict[str, str]:
    values = os.environ if env is None else env
    normalized_url = normalize_url(url)
    zone = (time_zone or values.get("ACA_JOB_DEFAULT_TIMEZONE", "Asia/Shanghai")).strip()
    cron = utc_cron(hour, minute, zone)
    config = load_config(values)
    name = job_name(normalized_url, config.name_prefix)
    request = ScheduledSummaryRequest(normalized_url, hour, minute, zone)
    payload = build_payload(config, request)
    uri = build_uri(config, name)

    token = credential_factory().get_token(ARM_SCOPE).token
    status, response_body = transport(
        "PUT",
        uri,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    if status not in {200, 201}:
        raise _azure_error(
            status,
            response_body,
            (config.registry_password, config.azure_openai_api_key, token),
        )
    try:
        response = json.loads(response_body) if response_body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = {}
    properties = response.get("properties", {}) if isinstance(response, dict) else {}
    return {
        "job_name": name,
        "resource_id": str(response.get("id", "")) if isinstance(response, dict) else "",
        "provisioning_state": str(properties.get("provisioningState", "")),
        "normalized_url": normalized_url,
        "utc_cron": cron,
        "time_zone": zone,
    }


def create_scheduled_website_summary(
    url: str, hour: int, minute: int, time_zone: str | None = None
) -> dict[str, str]:
    """Create or update the daily summary job for one explicit website URL."""
    return create_or_update_job(url, hour, minute, time_zone)
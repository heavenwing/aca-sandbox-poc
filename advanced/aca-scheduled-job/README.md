# ACA Scheduled Website Summary Advanced Experiment

本目录是独立的高级实验，不改变仓库根目录的 chart agent 和 sum-site agent POC。它增加一个标准问答 agent，并在用户明确提出每日网站摘要请求时，通过 ARM REST 创建或更新 ACA Scheduled Job。

## 安装

在本目录运行：

```powershell
uv sync
Copy-Item .env.example .env
az login
```

填写 `.env`。运行 `ask_agent.py` 的身份需要目标范围的 `Microsoft.App/jobs/read`、`Microsoft.App/jobs/write` 和 managed environment 关联权限。`ACA_JOB_IDENTITY_ID` 指向的已有用户分配托管身份需要执行 ACA Sandbox 的权限。

## 构建 Job 镜像

```powershell
docker build -t YOUR_REGISTRY.azurecr.io/aca-sum-site-job:sha-<commit> .
docker push YOUR_REGISTRY.azurecr.io/aca-sum-site-job:sha-<commit>
```

镜像以非 root 用户运行。仓库密码和 Azure OpenAI API key 进入 ACA Job secrets，容器只使用 `secretRef`。

## 运行

```powershell
uv run python ask_agent.py
```

普通问题不会写 Azure。示例定时请求：

```text
每天早上 8:00 总结 https://example.com
```

未指定时区时使用 `ACA_JOB_DEFAULT_TIMEZONE`。`08:00 Asia/Shanghai` 转换为 UTC cron `0 0 * * *`。当前只支持每日明确小时和分钟，并拒绝 UTC 偏移会变化的时区。同一规范化 URL 使用相同的稳定 Job 名称，因此再次请求会更新原 Job。

摘要会以 UTF-8 Markdown 文件写入 `SUM_SITE_OUTPUT_PATH`，默认目录为 `/mnt/output`。文件名由规范化 URL 和 UTC 时间戳组成，例如 `https-example-com-20260820T123456Z.md`。

创建 Job 时会把 ACA Managed Environment 中已有的持久存储挂载到输出目录：

- `ACA_JOB_OUTPUT_STORAGE_NAME`：Environment 中注册的 storage 名称（不是 Azure Files share 名称），必填。
- `ACA_JOB_OUTPUT_STORAGE_TYPE`：`AzureFile`（SMB，默认）或 `NfsAzureFile`（NFS），必须与 Environment storage 类型一致。
- `SUM_SITE_OUTPUT_PATH`：容器内绝对挂载路径，默认 `/mnt/output`。

该代码只引用已有的 Environment storage，不创建 Azure Files 或 Environment storage。挂载必须具有写权限，否则 Job 会以非零状态结束。

## 验证和排查

```powershell
uv run python -m py_compile ask_agent.py scheduled_summary_job.py sum_site_job.py
uv run pytest
az containerapp job show --name <JOB_NAME> --resource-group <ACA_JOB_RESOURCE_GROUP>
az containerapp job execution list --name <JOB_NAME> --resource-group <ACA_JOB_RESOURCE_GROUP> --output table
az containerapp job logs show --name <JOB_NAME> --resource-group <ACA_JOB_RESOURCE_GROUP> --execution <EXECUTION_NAME> --container sum-site
```

- `AuthorizationFailed`：检查当前 `az` 订阅及 Job/environment 权限。
- `ImagePullBackOff`：检查镜像名和 registry secret。
- Job 执行失败：检查 execution logs、OpenAI key、Sandbox 配置、Disk ID 和网站安全策略。
- 时区被拒绝：使用全年固定 UTC 偏移的 IANA 时区，例如 `Asia/Shanghai`。

清理时只删除本实验创建的单个 Job：

```powershell
az containerapp job delete --name <JOB_NAME> --resource-group <ACA_JOB_RESOURCE_GROUP> --yes
```

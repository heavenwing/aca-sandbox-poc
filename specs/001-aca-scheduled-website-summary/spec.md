# Feature Specification: ACA 定时网站摘要

**Feature Branch**: `001-aca-scheduled-website-summary`  
**Created**: 2026-08-18  
**Status**: Draft  
**Input**: 新增标准问答 console app，并根据用户请求创建定时运行网站摘要的 Azure Container Apps Job。

## User Scenarios & Testing

### User Story 1 - 标准问答 (Priority: P1)

作为 console app 用户，我希望直接提出一般性问题并获得回答，以便将该应用作为标准问答助手使用。

**Why this priority**: 标准问答是应用的基础能力，也是用户进入其他能力的统一入口。

**Independent Test**: 启动应用后提出一个不涉及定时任务的问题；应用直接返回答案，且不创建或更新任何 Azure 资源。

**Acceptance Scenarios**:

1. **Given** 应用已启动且问答模型配置有效，**When** 用户提出普通知识问题，**Then** 应用返回与问题相关的文本回答。
2. **Given** 用户请求不包含定时执行意图，**When** 应用处理请求，**Then** 应用不得调用定时 Job 创建工具。

---

### User Story 2 - 创建每日网站摘要任务 (Priority: P1)

作为 console app 用户，我希望用自然语言指定网站和每日执行时间，以便系统自动创建定时网站摘要任务。

**Why this priority**: 这是本功能的核心业务价值。

**Independent Test**: 输入“每天早上 8 点总结 `https://example.com`”；验证系统创建一个每日执行的 ACA Scheduled Job，Job 参数中包含规范化的网站 URL，并按默认时区换算为 UTC cron。

**Acceptance Scenarios**:

1. **Given** Azure 和应用环境变量均有效，**When** 用户明确要求每天 08:00 总结一个公开网站且未指定时区，**Then** 系统使用默认 `Asia/Shanghai` 时区创建 Scheduled Job。
2. **Given** 默认时区为 `Asia/Shanghai`，**When** 用户请求每天 08:00 执行，**Then** Job 使用 `0 0 * * *` UTC cron。
3. **Given** 用户提供的 URL 没有协议，**When** 系统创建任务，**Then** URL 被规范化为绝对 HTTPS URL 后通过 `SUM_SITE_URL` 环境变量传给 Job。
4. **Given** 用户没有提供 URL 或明确执行时间，**When** 系统处理定时请求，**Then** 系统要求补充缺失信息且不创建 Azure 资源。
5. **Given** URL 包含用户名或密码，或不是 HTTP(S) URL，**When** 系统处理请求，**Then** 系统拒绝创建 Job 并说明输入无效。

---

### User Story 3 - 幂等更新已有任务 (Priority: P2)

作为用户，我希望重复提交同一网站的定时摘要请求时更新已有任务，以免产生无法管理的重复 Job。

**Why this priority**: 稳定的资源命名和更新语义可降低资源浪费与运维成本。

**Independent Test**: 对同一规范化 URL 连续提交两个不同执行时间的请求；验证第二次操作更新同名 Job，Azure 中没有产生第二个资源。

**Acceptance Scenarios**:

1. **Given** 某 URL 已存在由本应用创建的 Job，**When** 用户修改该 URL 的执行时间，**Then** 系统更新同名 Job 的 cron 配置。
2. **Given** 两个输入 URL 规范化后相同，**When** 分别创建任务，**Then** 两次请求使用相同 Job 名称。
3. **Given** 两个规范化 URL 不同，**When** 分别创建任务，**Then** 系统为其使用不同 Job 名称。

---

### User Story 4 - 定时执行并记录摘要 (Priority: P2)

作为任务维护者，我希望 Scheduled Job 每次只总结一个网站并正常结束，以便从 ACA 执行历史和日志判断任务是否成功。

**Why this priority**: 有限执行和正确退出是 Scheduled Job 可观测、可重试的基础。

**Independent Test**: 使用 `SUM_SITE_URL` 启动 Job 镜像；验证程序不进入交互输入循环，只执行一次摘要、输出到标准输出并结束。

**Acceptance Scenarios**:

1. **Given** Job 容器设置了有效的 `SUM_SITE_URL`，**When** 容器启动，**Then** 程序执行一次网站摘要、将结果写入标准输出并退出成功。
2. **Given** 网站抓取或摘要失败，**When** Job 执行结束，**Then** 容器以非零状态退出，并且错误可从 Job 日志定位。
3. **Given** 本地启动时没有设置 `SUM_SITE_URL`，**When** 用户运行现有网站摘要程序，**Then** 程序保留交互式 console 行为。

### Edge Cases

- 用户使用相对时间、模糊时间或非“每日”周期时，系统不得猜测，应说明当前支持范围并要求明确的每日小时与分钟。
- 用户提供不存在或无效的 IANA 时区时，系统拒绝创建 Job。
- 用户指定采用夏令时且 UTC 偏移会变化的时区时，单个固定 UTC cron 无法全年保持相同本地时间；系统应拒绝该配置并解释限制。
- ARM 返回认证、授权、限流、输入错误或服务端错误时，系统只返回必要的状态、错误代码和消息，不泄露令牌、密码或完整请求体。
- Job 名称经过规范化后必须满足 ACA 资源命名约束，并避免不同 URL 之间发生可预见碰撞。
- 重复请求、进程重启或网络重试不得产生同一网站的重复 Job。
- 目标网站重定向到私网、环回或云元数据地址时，现有 Sandbox 抓取安全策略必须继续拒绝访问。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供名为 `ask_agent.py` 的交互式 console app。
- **FR-002**: 系统 MUST 对不涉及定时网站摘要的请求提供标准文本问答。
- **FR-003**: 系统 MUST 仅在用户明确请求定时网站摘要时调用 Azure Job 工具。
- **FR-004**: 系统 MUST 在创建任务前获得明确的网站 URL、每日执行小时和分钟。
- **FR-005**: 系统 MUST 仅接受绝对 HTTP(S) 网站 URL，并拒绝包含用户名或密码的 URL。
- **FR-006**: 系统 MUST 将未包含协议的有效域名输入规范化为 HTTPS URL。
- **FR-007**: 系统 MUST 从环境变量读取默认时区，未配置时使用 `Asia/Shanghai`。
- **FR-008**: 系统 MUST 将用户本地时间转换为 ACA Scheduled Job 使用的五字段 UTC cron。
- **FR-009**: 系统 MUST 拒绝无法通过单个固定 UTC cron 保持全年本地执行时间的配置。
- **FR-010**: 系统 MUST 通过 Azure Resource Manager REST API 创建或更新 ACA Scheduled Job。
- **FR-011**: 系统 MUST 从环境变量读取订阅、资源组、区域、Container Apps Environment、镜像、资源规格、超时和镜像仓库信息。
- **FR-012**: 系统 MUST 使用当前 Azure CLI 身份获取 ARM 访问令牌，且不得记录访问令牌。
- **FR-013**: 系统 MUST 为每个规范化 URL 生成稳定且符合 ACA 约束的 Job 名称。
- **FR-014**: 系统 MUST 使用幂等资源更新操作，使同一网站后续请求更新已有 Job。
- **FR-015**: 系统 MUST 将规范化 URL 作为 `SUM_SITE_URL` 环境变量传给 Job 容器。
- **FR-016**: Job MUST 使用 Schedule 触发器、单副本并行度和单副本成功完成条件。
- **FR-017**: 系统 MUST 将仓库密码和 Azure OpenAI API key 存为 ACA Job secret，并通过 `secretRef` 提供给容器。
- **FR-018**: 系统 MUST NOT 在日志、工具响应或普通环境变量值中暴露密钥、仓库密码或 ARM 令牌。
- **FR-019**: 网站摘要程序 MUST 在检测到 `SUM_SITE_URL` 时仅执行一次摘要并退出。
- **FR-020**: 网站摘要程序 MUST 在没有 `SUM_SITE_URL` 时保留现有交互模式。
- **FR-021**: Job 执行成功时 MUST 将摘要写入标准输出；失败时 MUST 返回非零退出状态。
- **FR-022**: 系统 MUST 继续使用现有固定 Sandbox runner 及其 URL、DNS、重定向和响应大小防护。
- **FR-023**: 项目 MUST 提供独立 Dockerfile，用于构建运行网站摘要程序的 Scheduled Job 镜像。
- **FR-024**: Job 镜像 MUST 使用非 root 用户运行，且不得包含 `.env`、本地输出或开发凭据。
- **FR-025**: `.env.example` MUST 列出新增配置，并且只包含安全默认值或占位符。
- **FR-026**: README MUST 包含镜像构建与推送、配置、运行 ask agent、验证 Job、查看执行历史和日志、故障排查及单个 Job 清理步骤。
- **FR-027**: Azure Job 工具 MUST 仅支持创建或更新本功能管理的 Scheduled Job，不得提供任意 ARM 请求、删除、启动或停止能力。

### Key Entities

- **Scheduled Summary Request**: 用户意图的结构化表示，包含规范化 URL、每日小时、分钟和 IANA 时区。
- **Schedule Conversion**: 本地每日时间与 ACA 五字段 UTC cron 之间的确定性映射。
- **Scheduled Summary Job**: 由应用管理的 ACA Job，包含稳定名称、触发配置、镜像、资源限制、环境变量和 secret 引用。
- **Job Runtime Input**: Job 容器运行一次摘要所需的配置，核心参数为 `SUM_SITE_URL`。
- **Tool Result**: 返回给 agent 的最小结果，包含 Job 名称、资源 ID 或配置状态、规范化 URL、UTC cron 和时区，不包含任何 secret。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在本地自动化测试中，100% 的普通问答请求不会触发 Azure Job 写操作。
- **SC-002**: 对有效的每日摘要请求，系统在一次工具调用内生成包含正确 URL 和 UTC cron 的 Job 请求。
- **SC-003**: `08:00 Asia/Shanghai` 在所有自动化测试中稳定转换为 `0 0 * * *`。
- **SC-004**: 对同一规范化 URL 连续提交请求时，100% 使用相同 Job 名称且不会创建重复资源。
- **SC-005**: 单次执行模式在一次摘要完成后退出，不等待 console 输入。
- **SC-006**: 自动化测试确认所有敏感配置只通过 secret 引用传入，工具输出中不出现 secret 原文。
- **SC-007**: 所有现有测试继续通过，且新增功能的 URL、时区、cron、命名、REST payload、错误处理和单次执行路径均有自动化覆盖。
- **SC-008**: 按 README 从全新本地配置完成镜像构建和 ask agent 启动时，不需要阅读源代码即可完成操作。

## Assumptions

- Azure 订阅、资源组、Container Apps Environment、镜像仓库、Azure OpenAI 资源、ACA Sandbox Group 和 Sandbox Disk 已存在。
- 首个版本只支持每日固定小时和分钟，不支持周、月、节假日或复杂日历表达式。
- 定时摘要结果以 ACA Job 日志为交付位置；邮件、Teams、Blob Storage 或数据库持久化不在本功能范围内。
- 运行 `ask_agent.py` 的身份已获得创建和读取 Job 以及关联目标 Container Apps Environment 所需的最小权限。
- 私有仓库首版按已确认方案使用用户名和密码；生产环境可后续迁移到具有 `AcrPull` 权限的用户分配托管身份。

## Out of Scope

- 创建或部署 Azure 基础设施。
- 自动分配 Azure RBAC 角色。
- 删除、暂停、恢复或立即启动 Job。
- 管理任意类型的 ACA Job 或接受用户提供的任意 ARM JSON。
- 向外部渠道发送摘要。
- 支持具有夏令时变化的全年固定本地执行时间。

# Context Keeper v1.x 契约（冻结）

本文件把 v1.x 对外可见的行为写成可核对的条目：命令面、退出码、载荷字段与上限、恢复视图、持久化与并发、部署清单、兼容规则。它描述**运行时已实现**的行为（基线 v1.1.3，commit `fdab9fd`，`schema_version: 1`，测试 49 项），不是计划。

改动任何一条即为破坏兼容：要么出 v2（`schema_version: 2` + 新存储目录语义），要么在本文档里显式记一条「契约变更」并升 minor。

## 1. 命令面

运行时入口 `scripts/context_cli.py`（仅标准库；Python 3.10+；Git 可选）。

| 命令 | 必填参数 | 可选参数 | 成功产物 |
|---|---|---|---|
| `status` | `--project` | — | manifest、`last_capture_id`、`record_count`、git 快照；未初始化时 `initialized:false`，exit 0 |
| `init` | `--project` | `--input <profile.json>` | `.context-keeper/` + 首快照；已存在 → `ALREADY_INITIALIZED`（exit 5） |
| `capture` | `--project --input <payload.json>` | — | 新快照，`last_capture_id` 更新 |
| `restore` | `--project` | `--budget short\|normal\|detailed`（默认 `normal`）、`--task <ID/标题子串>`、`--record <ID>`、`--at <capture_id>` | 项目视角 / 单任务 / 单记录 / 历史快照 |
| `validate` | `--project` | — | `level` = `PASS`/`WARNING`、`findings`、`history_checked` |
| `fingerprint` | `--project --path <项目相对路径>` | — | 该路径的 SHA-256 |

互斥与前置：`--record` 与 `--task` 不能同用；`--at` 必须配 `--record` 或 `--task`。`restore` 与 `validate` 要求已初始化；`status`/`fingerprint` 在未初始化时按空库处理。

两个开发工具（**不随技能部署**）：`scripts/sync_deploy.py`（安装/`--check`）、`scripts/release_checksums.py`（发版校验单）。

## 2. 退出码

| 退出码 | 触发条件 |
|---|---|
| 0 | 命令成功。响应 JSON 仍可能带 `level: WARNING` 与 `findings`。 |
| 1 | 不使用。CLI 的任何已知失败都不会返回 1。 |
| 2 | 参数不合法（`USAGE`）：缺参数、`--record`+`--task` 同用、`--at` 单独使用、`--task` 空串。 |
| 3 | `validate` 查到至少一条 finding（`level: WARNING`）。 |
| 4 | 数据 / 证据 / 路径 / IO 错误（见下）。 |
| 5 | 状态冲突：`ALREADY_INITIALIZED`、`WRITE_BUSY`、`CAPTURE_CONFLICT`、`RECORD_CONFLICT`。 |

映射来源：`scripts/context_cli.py:320-338`（`USAGE`→2；四个冲突码→5；其余 `KeeperError`→4；`OSError`/`UnicodeError`/`ValueError`/`RecursionError` → 4，其中 `FileNotFoundError`→`MISSING_FILE`、`PermissionError`→`FILE_ACCESS`、其余→`IO_OR_DATA_ERROR`）。`validate` 的 3 由 `context_cli.py:285` 直接返回；`sync_deploy.py` 成功 0、失败 4（`sync_deploy.py:113,119`）。

exit 4 的代表码（同一退出码下再按 `error.code` 区分）：

- 载荷类：`INVALID_JSON`、`INVALID_SCHEMA`、`INVALID_RECORD`、`INVALID_EVIDENCE`、`DUPLICATE_ID`、`DUPLICATE_RECORD`、`INVALID_SUPERSESSION`、`INVALID_TARGET`、`SIZE_LIMIT`、`UNSUPPORTED_VERSION`；
- 证据类：`EVIDENCE_NOT_VERIFIED`、`HASH_MISMATCH`、`FILE_CHANGED`、`CAPTURE_NOT_FOUND`、`RECORD_NOT_FOUND`；
- 安全类：`UNSAFE_PATH`、`UNSAFE_INPUT`、`SENSITIVE_PATH`、`SENSITIVE_DATA`、`INVALID_SOURCE`；
- Git 类：`GIT_UNAVAILABLE`、`GIT_BOUNDARY`、`GIT_LIMIT`、`GIT_COMMAND_FAILED`；
- 库/IO 类：`CORRUPT_STORE`；
- 选择器类：`TASK_AMBIGUOUS`（响应带 `error.candidates`）、`TASK_NOT_FOUND`。

响应恒为单行 JSON：`{"ok":true, "command":…, …}` 或 `{"ok":false, "command":…, "level":"FAIL", "error":{code,message,…}}`，且 `ensure_ascii=true`（`context_cli.py:340`）。

## 3. capture 载荷

顶层三键全部必填，`additionalProperties:false`：

```json
{"expected_capture_id": "<uuid> | null", "records": [...], "handoff": {...}}
```

- `expected_capture_id`：首次 capture 为 `null`；之后必须等于 `status` 返回的 `last_capture_id`，否则 `CAPTURE_CONFLICT`。
- `records`：0–150 条；已有 ID 的更新必须带 `expected_updated_at`（取 `restore --record ID` 的 `updated_at`），新记录带它会被拒。

单条记录字段：

| 字段 | 必填 | 规则 |
|---|---|---|
| `id` | 是 | `^[a-z][a-z0-9_-]{0,63}$`，≤64；同 ID 只代表同一对象 |
| `type` | 是 | `goal` / `state` / `decision` / `issue` / `task` |
| `title` | 是 | 1–120 字符 |
| `content` | 是 | 1–720 字符 |
| `status` | 是 | `VERIFIED` / `REPORTED` / `UNVERIFIED` / `CONFLICTED` / `STALE` |
| `evidence` | 是 | 0–12 条（见下） |
| `task_status` | 否 | `TODO` / `IN_PROGRESS` / `BLOCKED` / `DONE` / `CANCELLED`；`DONE`、`CANCELLED` 视为终态 |
| `next_steps` | 否 | 0–12 条，每条 1–240；仅 `task`、`issue` 有效，是**该记录自己的**计划 |
| `priority` | 否 | 1–3，1 最高，缺省 2 |
| `claim_key` + `position` | 否 | `claim_key` 同 id 规则；`position` 1–180。同 `claim_key` 下两个不同活跃 `position` 即冲突 |
| `supersedes` | 否 | 0–10 个 id，替旧 `decision` |
| `expected_updated_at` | 否 | ISO 8601，带时区；仅用于更新已有 ID |

`decision` 只增不改：改标题/内容/`claim_key`/`position` 报 `RECORD_CONFLICT`；改写结论用新 ID + `supersedes`。

evidence 单条：`kind` + `locator`（1–300）必填，`additionalProperties:false`。`kind ∈ {file,test,git_commit,user,session,report,task}`。按 kind 追加要求：

- `file` / `test`：`path`（项目相对、正斜杠、≤300），建议带 `sha256`（64 位小写 hex）与一基 `line`；`test` 另需 `exit_code`，VERIFIED DONE 还要求 `observed_at` 且 exit 0；
- `git_commit`：`commit`（40 或 64 位 hex），须在 HEAD 可达的最近 20 个提交内，否则 `GIT_BOUNDARY`；
- `user` / `session` / `report` / `task`：只用 `locator` 说明来源，CLI 不跟随 URL。

`handoff` 必填三键：`scope{sources,limits}`（`sources` 为 1–7 个枚举 `visible_session/user_export/user_input/project_files/git/test_run/task_tracker`；`limits` 1–360）、`summary`（1–420 或 `null`）、`next_steps`（0–12 条，每条 1–240）。`handoff` 是**项目级**唯一便签，每次 capture 整体替换。

## 4. 恢复视图

项目视角（默认）顶层键固定：`budget`、`project{id,name,goal,goal_source,tech_stack}`、`capture_id`、`project_note{summary,next_steps,scope}`、`sections{current_state,open_issues,verified_progress,key_decisions}`、`potentially_stale(+_count)`、`conflicted(+_count)`、`records_with_steps(+_count)`、`validation_level`、`findings`、`finding_count`、`omitted_records`、`omitted_project_steps`、`trust`，`detailed` 时另有 `history`，记录无 `next_steps` 的单任务视图另有 `next_steps_note`。

- 顶层**没有**裸 `next_steps`；任务自己的步骤只在 `restore --task` 出现，或按 ID 列在 `records_with_steps`。
- `sections` 归类：`current_state ← {goal,state,task}`；`open_issues ← {issue,task}` 中非终态或 `BLOCKED`；`verified_progress ← {state,task}` 中 `effective_status=VERIFIED` 且 `DONE`/`state`；`key_decisions ← {decision}`（`detailed` 下含已退休决策）。
- 三档预算（`context_cli.py:19-23`）：`short` = 2800 字符 / 8 条 / 0 条历史；`normal` = 9000 / 22 / 0；`detailed` = 24000 / 45 / 4。项目便签步骤的条数上限 `short 2`、`normal 5`、`detailed 12`。预算按记录整体取舍，不截断单条记录内容。
- 截断计数：`omitted_records` 只统计记录；`omitted_project_steps` 只统计 `handoff.next_steps` 被预算截掉的条数——它不是「库里没有别的步骤」的证据。
- 列表上限：`potentially_stale`、`conflicted`、`records_with_steps`、`findings` 各最多 12 条，`*_count` 给全量。
- 单任务视图（`--task`）：只回该记录的 `content` 与它自己的 `next_steps`，不带项目 goal/便签/其他记录；`--budget` 被回显但不裁剪。`next_steps_source: "missing"` 表示记录早于按任务步骤的版本，此时读 `content`。

## 5. 持久化与并发

- 目录：`.context-keeper/` 下 `manifest.json`（唯一发布点）、`snapshots/<sha256>.json`、`records/<sha256>.json`、`write.lock`。对象名 = 其 UTF-8 JSON 字节的 SHA-256。
- 写入：同目录临时文件 + `os.replace` 原子替换；先写不可变对象，最后写 manifest；失败或中断最多留下不可达对象，下一次快照从已发布 manifest 续链。
- 锁：写操作全程持 `write.lock` 的排它**非阻塞**锁（Windows `msvcrt.locking`，POSIX `fcntl.flock`），进程退出即释放。拿不到锁立即 `WRITE_BUSY`（exit 5），不进临界区。
- 冲突：锁内重读 manifest 与 `expected_capture_id` 比对；陈旧 ID → `CAPTURE_CONFLICT`（exit 5），在写任何对象之前失败。发布前再读一次 manifest 比对，不一致同样 `CAPTURE_CONFLICT`。
- 幂等：同 Git 身份下重发字节一致的载荷 → 视为重复，返回 `changed:false`，不新增快照。
- 不自动重试：失败后重新 `status` 取最新 `last_capture_id`，由调用方重发。

## 6. 部署清单（13 个运行时文件）

`scripts/sync_deploy.py` 的 `FILES` 是唯一清单，`inventory()` 按字节哈希全等校验：

```
SKILL.md
scripts/context_cli.py
scripts/git_inspector.py
scripts/sanitizer.py
scripts/storage.py
scripts/sync_deploy.py
scripts/validator.py
references/context.schema.json
references/data-schema.md
references/capture-policy.md
references/restore-policy.md
references/evidence-policy.md
references/security-policy.md
```

目标目录：DSH `%USERPROFILE%\.dsh\skills\context-keeper`、Codex `%USERPROFILE%\.agents\skills\context-keeper`；备份在技能目录上一级 `.context-keeper-deploy/`。不在清单里的文件（`tests/`、`README.md`、`TEST-REPORT.md`、`CONTRACT-v1.x.md`、`scripts/release_checksums.py`）**不会**被安装；新增要部署的文件必须先加进 `FILES`。

## 7. 兼容规则

v1.x 内允许（minor）：

- 新增可选记录字段、新增 evidence `kind`、新增错误码（沿用既有 exit 语义）、新增诊断字段；
- 新增命令或新参数（旧调用不变）；
- 文档、测试、部署工具变化，运行时行为不变。

v1.x 内**不允许**（须 v2）：

- 改 `schema_version` 或存储目录布局；
- 改已有字段名/枚举值/长度上限，或把可选字段变必填；
- 改退出码映射或既有错误码含义；
- 把 `handoff` 改成可承载多任务，或把 `next_steps` 挪到记录之外；
- 改 `records_with_steps` / `omitted_project_steps` 的计数口径。

## 契约变更记录

- 2026-11-17 · v1.1.3 基线：并发语义入 `references/data-schema.md`；`release_checksums.py` 生成部署清单校验单。运行时行为相对 v1.1.2 无变化。
- 2026-11-17 · 本文档补入 main（v1.1.3 之后，未改动 `FILES`、未改运行时），把上述行为逐条冻结。

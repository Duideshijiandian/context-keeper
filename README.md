# Context Keeper（v1.1.3）

为每个项目维护一份可验证的开发上下文。Agent 负责提取语义、核对证据和撰写交接；Python CLI（仅标准库运行时）负责项目边界、Git 状态、输入校验、增量持久化与复核。恢复时只信 `capture_id` 指向的那份快照，并逐条检查证据是否还站得住。

## 快速开始

需要 Python 3.10+，Git 可选。命令输出的 JSON 使用 ASCII 转义，任何 Windows 终端编码都能解析。

```powershell
$cli = 'F:/path/to/context-keeper/scripts/context_cli.py'
$project = 'F:/path/to/project'
python -X utf8 $cli status    --project $project
python -X utf8 $cli init      --project $project            # 首次；可加 --input profile.json
python -X utf8 $cli capture   --project $project --input payload.json
python -X utf8 $cli restore   --project $project --budget normal
python -X utf8 $cli restore   --project $project --task "CSV 导入去重"
python -X utf8 $cli validate  --project $project
```

| 命令 | 作用 |
|---|---|
| `init` | 建 `.context-keeper/`，写首个快照与 manifest。已存在则报 `ALREADY_INITIALIZED`（exit 5），要先归档再删目录。 |
| `capture` | 追加记录并发布新快照。必须带 `expected_capture_id`（首次为 `null`，之后用 `status` 的 `last_capture_id`）。 |
| `restore` | 恢复项目视角（默认）或单个记录。`--task <ID/标题子串>` 只回该任务的摘要与下一步；`--record <ID>` 回完整记录与证据；`--at <capture_id>` 回历史快照。 |
| `validate` | 复核证据锚点、Git 状态与历史链。有任何 finding 返回 `level=WARNING`、exit 3。 |
| `status` | 当前 manifest、`last_capture_id`、记录数。 |
| `fingerprint` | 给项目相对路径算 SHA-256，供 file/test 证据引用。 |

| 退出码 | 含义 |
|---|---|
| 0 | 成功（JSON 里仍可能有 warning）。 |
| 2 | CLI 参数不合法（`USAGE`）。 |
| 3 | `validate` 结果为 `WARNING`。 |
| 4 | 数据 / 证据 / 路径安全 / IO 错误（`INVALID_SCHEMA`、`INVALID_RECORD`、`INVALID_EVIDENCE`、`UNSAFE_PATH`、`TASK_AMBIGUOUS`、`TASK_NOT_FOUND`、`CAPTURE_NOT_FOUND`、`DUPLICATE_RECORD`…）。 |
| 5 | 状态冲突：`ALREADY_INITIALIZED`、`WRITE_BUSY`、`CAPTURE_CONFLICT`、`RECORD_CONFLICT`。 |

## 数据模型要点（v1.1.x）

- 记录类型：`state` / `task` / `issue` / `decision`。`next_steps` 只允许挂在 `task` 与 `issue` 上，是**该任务自己的计划**；项目级计划只在 `handoff`。
- `restore` 项目视角的长这样：`project_note {summary, next_steps, scope:{limits,sources}}`，顶层没有裸 `next_steps`；`records_with_steps` / `records_with_steps_count` 索引「哪些记录自带步骤」，正文不展开——要看某任务的步骤用 `restore --task`。`omitted_project_steps` 只统计项目便签自己的步骤被预算截掉几条。
- 新记录不能带 `expected_updated_at`；原地更新已有 ID 时**必须**带当前 `updated_at`（先 `restore --record ID` 拿）。
- `decision` 是只增不改的：改结论要用新 ID，旧记录靠 `supersedes` 承接。改已有 decision 的标题/内容/`claim_key`/`position` 会报 `RECORD_CONFLICT`。
- `handoff.scope.sources` 枚举只有 `visible_session / user_export / user_input / project_files / git / test_run / task_tracker`。
- 长度上限：`title` ≤120、`content` ≤720、`handoff.summary` ≤420、`scope.limits` ≤360、`next_steps` 每条 ≤240 且 ≤12 条、一次 capture ≤150 条记录。
- `init` 的 `exclude_paths` 必须写成项目相对、正斜杠（`node_modules/.cache`），反斜杠报 `UNSAFE_PATH`（exit 4）。

## 并发语义

写操作在 `.context-keeper/write.lock` 上取排它锁（Windows `msvcrt.locking`，POSIX `fcntl.flock`），且是**非阻塞**的：

- 两个真正重叠的 capture，后到者拿不到锁 → `WRITE_BUSY`（exit 5），根本没进临界区。
- 后启动的进程读到已发布的 manifest，`expected_capture_id` 已过期 → `CAPTURE_CONFLICT`（exit 5）。
- 两条失败路径都在写 `records/` 与 `snapshots/` 之前，所以**不会**留下半截快照或不可达以外的副作用；发布前还会再读一次 manifest 比对（`storage.py:340-341`）。
- 想重试就重新 `status` 拿最新 `capture_id` 再 capture，CLI 不自动重试。

实现细节与代码锚点（锁、陈旧 `expected_capture_id`、发布前二次比对、幂等重发）见 [数据模型](references/data-schema.md) 的 Concurrent writers 一节；本节的实测结论与之逐条对应。

实测（2026-11-17）：12 轮真实双进程对撞，失败方 12/12 为 `WRITE_BUSY`；10 轮对撞 + 1 轮四进程的自动化用例全部恰好一次提交，`validate` 保持 `PASS`，`snapshots/`、`records/` 每个文件 SHA-256 = 文件名，无 `.pending-*` 残留。用例见 `tests/test_concurrent_capture.py`，结论见场景交付文档。

## 维护与安装（六步）

1. 改工作副本（唯一可改目录）。
2. 新增运行时文件必须同步进 `scripts/sync_deploy.py` 的 `FILES` 元组，否则装不上。
3. 跑测试，判据是 `Ran N tests ... OK`（当前 49 项）：

   ```powershell
   $env:PYTHONPATH = 'F:/path/to/test-pydeps'   # 仅静态测试需要 PyYAML / jsonschema
   python -X utf8 -B -m unittest discover -s tests -t tests
   ```

4. 备份到 `F:\AI\03_备份\YYYY-MM-DD-<事项>\`，写 `清单.md`（每文件 SHA-256 + 回滚命令）。
5. 两个宿主各部署一次，再 `--check` 验收（脚本自带二次备份，只装 13 个运行时文件）：

   ```powershell
   python -X utf8 -B scripts/sync_deploy.py --skills-dir "$env:USERPROFILE/.dsh/skills"
   python -X utf8 -B scripts/sync_deploy.py --skills-dir "$env:USERPROFILE/.dsh/skills" --check
   python -X utf8 -B scripts/sync_deploy.py --skills-dir "$env:USERPROFILE/.agents/skills"
   python -X utf8 -B scripts/sync_deploy.py --skills-dir "$env:USERPROFILE/.agents/skills" --check
   ```

6. 生效验证：开**新会话**用 `skill` 工具加载，确认正文含新句子 + `Base directory` 指向该宿主目录。`skill` 返回的正文不含 frontmatter，看不到 `version:`；deploy 后不需要重启 DSH（skill-filesystem 有 watcher）。

安装布局（本机验证过）：Codex `%USERPROFILE%\.agents\skills\context-keeper`，DSH `%USERPROFILE%\.dsh\skills\context-keeper`。备份在技能目录上一级 `.context-keeper-deploy`；替换的两次目录重命名之间断电，可按部署报告与该备份人工恢复。

## 发版与回滚

每次 `vX.Y.Z` 发布，除 tag 与说明外必须附「运行时文件校验单」与回滚命令；校验单由脚本从 `FILES` 生成，不手抄：

```powershell
python -X utf8 -B scripts/release_checksums.py --version X.Y.Z --previous X.Y.<上一版> --output ..\SHA256SUMS-vX.Y.Z.txt
```

- 校验单每行 `<sha256>  <仓库相对路径>`，覆盖 13 个运行时文件；在仓库根可直接 `sha256sum -c ..\SHA256SUMS-vX.Y.Z.txt`（Git Bash），PowerShell 用 `Get-FileHash -Algorithm SHA256` 逐文件对照。
- release 说明里贴校验单、回滚段与「仓库私有」提示，校验单同时作为 release asset 上传。私有仓库的 `/releases/tag/...` 对未登录访客返回 404，要公开分发得另建公开说明页或迁公开仓库。
- 回滚：`git -C <工作副本> checkout v<上一版>`，再对两个宿主重跑 `sync_deploy.py` 与 `--check`（脚本自带二次备份；`git checkout` 不动已装目录，必须重新部署）。

## 测试与已知坑

- 49 项自动化测试（`tests/`）：存储、恢复、按任务恢复、安全、部署一致性与并发压测、发版校验单。测试只用临时目录与故障注入，不碰真实项目。
- PowerShell 下 unittest 的进度点走 stderr，会把整条命令显示成 `[exit code: 1]` 的假失败；判据只看 `Ran N tests ... OK` / `FAILED`。
- `validate` 报 `EVIDENCE_CHANGED` 常见于 file 证据锚了逐轮覆盖写的文件（如交接文档）；decision 又不可原地改，通常只能接受为提示。要避免就别把 file 证据锚在每轮都会变的文件上。
- 别在 capture 期间手工编辑或移动 `.context-keeper/`；manifest 是唯一发布点，旧快照与记录保持可读。

## 可恢复性与边界

- 文件写入用同目录临时文件 + `os.replace` 原子替换；发布前中断只会留下不可达对象。旧快照始终可读，`--at <capture_id>` 可取回。
- Git 只读显式根目录下的本地仓库，工作树状态经过敏感路径 / 依赖缓存 / `exclude_paths` 过滤。Windows 上未设 `core.autocrlf` 时按 Git for Windows 的 CRLF 行为。
- 证据哈希只能判断「内容变没变」；决策含义、测试覆盖范围、历史来源仍需 Agent 判断。
- 敏感信息检测是启发式的：别把原始聊天、客户数据或凭证放进载荷。

更多细节：[数据模型](references/data-schema.md) · [采集策略](references/capture-policy.md) · [恢复策略](references/restore-policy.md) · [证据策略](references/evidence-policy.md) · [安全策略](references/security-policy.md) · [行为场景](tests/scenarios.json)。

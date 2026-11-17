# Context Keeper V1.0

Context Keeper 为每个项目维护一份可验证的开发上下文。Agent 负责提取语义、核对证据和撰写交接，Python CLI 负责项目边界、Git 状态、输入校验、增量持久化和复核。

## 快速开始

需要 Python 3.10+。Git 可选。Windows 下使用 PowerShell，把示例根目录替换成明确授权的项目。

```powershell
$cli = 'F:/dsharness/context-keeper/scripts/context_cli.py'
$project = 'F:/path/to/project'
python -B $cli status --project $project
python -B $cli init --project $project
python -B $cli capture --project $project --input 'F:/path/to/sanitized-payload.json'
python -B $cli restore --project $project --budget normal
python -B $cli validate --project $project
python -B $cli restore --project $project --record import-identity
```

按 [数据模型](references/data-schema.md) 的示例生成 payload。用 `status` 返回的 `last_capture_id` 作为 `expected_capture_id`。首次采集使用 `null`。更新已有记录时，先用 `restore --record ID` 获取 `updated_at`。用 `fingerprint --project ROOT --path RELATIVE` 获取被允许证据文件的 SHA-256。命令本身会输出 JSON，使用 ASCII 转义保证不同 Windows 终端编码下仍可解析。

| 退出码 | 含义 |
|---|---|
| 0 | 操作成功。INIT、CAPTURE、RESTORE 和 STATUS 的 JSON 仍可能包含警告。 |
| 2 | CLI 参数不合法。 |
| 3 | VALIDATE 返回 WARNING。 |
| 4 | 数据、证据安全边界或文件访问错误。 |
| 5 | 已初始化、并发写入或更新前置条件冲突。 |

### 在 Agent 中调用

可以说「初始化当前项目的上下文管理」「保存本次开发进度并生成下次交接」「恢复这个项目的历史状态」或「检查上下文是否过时」。Codex 也支持显式使用 `$context-keeper`。恢复会报告核验范围、已过时的依据和待复核项。普通编程和技术问答无需调用。

## 安装与同步

将 `--skills-dir` 指向宿主实际使用且已经存在的技能目录。部署脚本只安装运行时清单中的 13 个文件，保留同名现有技能的完整备份，逐文件核对 SHA-256。用 `--check` 单独验收。

```powershell
$deploy = 'F:/dsharness/context-keeper/scripts/sync_deploy.py'
python -B $deploy --skills-dir "$env:USERPROFILE/.agents/skills"
python -B $deploy --skills-dir "$env:USERPROFILE/.agents/skills" --check
python -B $deploy --skills-dir "$env:USERPROFILE/.dsh/skills"
python -B $deploy --skills-dir "$env:USERPROFILE/.dsh/skills" --check
```

以上路径对应本机验证过的安装布局。若 Windows 沙箱隐藏 Python 或已安装技能，使用沙箱允许的明确解释器路径和对应技能目录。在 `codex exec` 的实验中使用了两个精确的 `--add-dir` 目录。备份位于技能目录的上一级 `.context-keeper-deploy` 中。断电发生在替换的两次目录重命名之间时，可按部署报告和该目录中保留的备份人工恢复。

## 可恢复性与验证边界

- `.context-keeper/manifest.json` 是发布点。旧快照及记录保持可读，发布前中断只会留下不可达对象。文件写入使用同目录临时文件、刷新和原子替换。Windows 文件占用会报错，已发布版本可以继续读取。避免在写入时手工编辑或移动存储目录。
- Git 只读取显式根目录的本地仓库。工作树状态是排除上下文、常见敏感路径、依赖缓存和自定义排除之后的视图。Windows 下项目没有本地 `core.autocrlf` 时使用 Git for Windows 常见的 CRLF 转换行为。仓库采用不同设置时，可在仓库的本地配置中明确约定。
- 证据哈希能判断内容是否变化。技术决策的含义、测试覆盖范围和历史来源仍需 Agent 判断。非 Git 项目可通过文件和日志锚点检查，未引用的项目变更不在检查范围内。
- 敏感信息检测是启发式的。建议将项目上下文排除出 Git，避免在载荷中放入原始聊天、客户数据或凭证。更多细节见 [安全策略](references/security-policy.md)。

## 自动化测试

仅静态测试依赖 PyYAML 和 jsonschema。运行时使用标准库。测试在临时目录创建仓库或故障样例。

```powershell
python -m pip install -r 'F:/dsharness/context-keeper/tests/requirements.txt'
python -X utf8 -B -m unittest discover -s 'F:/dsharness/context-keeper/tests' -v
```

[行为场景](tests/scenarios.json) 提供触发、误触发、证据与安全的可复测提示词。真实模型评估以独立会话的原始回答和工具轨迹为准。

# Context Keeper V1.0 验收报告

2026 年 11 月 8 日，北京时间。验证环境为 Windows 11 25H2，Python 3.12.10，Git for Windows 2.54.0，Codex CLI 0.167.0-alpha.3.2 和 DSHarness 0.2.2-rc.1。

## 验收结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| INIT 和非 Git 支持 | PASS | 隔离 Git 项目与非 Git 目录均初始化成功。重复 INIT 保留原存储。Unicode 路径和项目重命名通过。 |
| 证据化 CAPTURE | PASS | 独立 Session A 实际编写 p50/p95、运行测试并保存日志哈希。另一个会话把用户口述的完成保存为 REPORTED / DONE，并收到缺少测试的警告。 |
| 跨会话 RESTORE | PASS | 新 Codex 会话仅使用项目路径和已安装技能，恢复目标、进展、决策、开放问题和下一步。最终安装版也通过中文自然语言恢复测试。 |
| VALIDATE 与历史追溯 | PASS | 真实 HEAD 变化触发 WARNING，原 VERIFIED 记录当前显示为 STALE。自动化测试覆盖缺失证据、损坏对象、冲突和被取代决策。 |
| 写入与安全边界 | PASS（已测范围） | 临时文件、发布失败、并发锁、目录联接、硬链接、路径穿越、外部 Git 配置、可疑凭证和本地 Git 可执行文件注入均有测试。 |
| 自动化测试 | PASS | 37 项全部通过，Skill Creator 的格式验证通过。JSON Schema 同时用 Draft 2020-12 验证器检查。 |
| 本机安装文件一致 | PASS | Codex 与 DSHarness 的 13 个运行时文件均通过源文件 SHA-256 复核。部署脚本保留了此前的同名 Context Keeper 版本。 |
| DSHarness 原生模型恢复 | NOT VERIFIED | Headless 启动后返回 `MISSING_CREDENTIAL`，默认 `deepseek-official` 模型路由缺少凭证。安装和文件一致性已独立验证。 |
| 其他宿主、真实业务项目、突然断电和敌对并发 | NOT VERIFIED | 测试只操作隔离项目。发布失败与残留临时文件用故障注入测试。 |

源码位于 `F:\dsharness\context-keeper`。Codex 使用 `%USERPROFILE%\.agents\skills\context-keeper`，DSHarness 使用 `%USERPROFILE%\.dsh\skills\context-keeper`。Codex 目录与官方文档一致，DSHarness 目录由本机安装、已有技能和 headless 启动确认。技能入口按 Agent Skills 开放规范编写。 [OpenAI Docs](https://learn.chatgpt.com/docs/build-skills) · [Agent Skills 规范](https://agentskills.io/specification)

## 真实跨会话实验

1. Session A 在合成项目中实现调度延迟 p50/p95，运行三组检查并将真实退出码、源码和日志 SHA-256 纳入记录。它保存了零运行时依赖决策及隔离网络理由、p99 未解决事项和下一步回归测试。采集 ID 为 `53d30758-79f7-4e57-80e6-730b6477e879`。
2. 随后在隔离仓库增加一个空提交，使 HEAD 从 `fbb54044d50408d87f6158568d856bfdb7c8d746` 变为 `a409b6f65c2dafb470dc81ecd3e222448b62672e`。验证发现 `GIT_HEAD_CHANGED` 与 `RECORD_NEEDS_RECHECK`，并报告原有两项未跟踪文件。原始测试日志未变化。
3. 同一项目分别交给全新的 Control 与 Treatment 会话。两组使用同一基础任务、结构化回复字段和只读约束。Control 可读普通项目文件、Git 和测试日志。Treatment 使用 Context Keeper。两组都排除临时载荷。对照组工具轨迹证实它未读取上下文记录，恢复组调用了 `restore` 与 `restore --record latency-delivery`。

| 指标 | Control | Treatment |
|---|---:|---:|
| 预先固定规则命中的字段 | 2 / 10 | 9 / 10 |
| 留空字段 | 4 | 0 |
| 已填但未匹配的字段 | 4 | 1 |
| 人工对照发现的明确虚构事实 | 0 | 0 |
| 用户交互轮数 | 1 | 1 |
| 工具命令数 | 4 | 3 |
| 宿主报告输入 Token | 78,569 | 82,333 |
| 宿主报告缓存输入 Token | 57,984 | 60,160 |
| 宿主报告输出 Token | 684 | 714 |
| 重跑测试或重复实现 | 0 | 0 |

字段匹配脚本在运行前已确定，结果可从原始回复重算。它对措辞敏感，Control 的复数形式 `latencies` 导致目标字段未命中，Treatment 对历史 VERIFIED 与当前未验证的正确区分也未通过置信字段的字面匹配。人工检查确认 Treatment 恢复了依赖决策的理由、p99 状态、下一步和交接 ID。Control 从项目文件推断出目标和实现，但把另一项代码设计选择作为决策来回答。明确虚构事实计数是对两份原始回答的人工核对，Token 为宿主实测。两组都按提示不重跑测试或重新实现，因此这次实验无法衡量在不限操作的场景下是否减少重复劳动。

最初的 Codex 启动受 Windows 沙箱影响，无法读取 Python 和技能文件。重试仅开放了这两个目录。第一次成功采集又发现私有暂存目录的 ACL 在发布后阻止普通进程访问；改用继承项目 ACL 的暂存目录后，使用新隔离项目完成上述实验。实验后增加了 Git 可执行文件边界检查、存储目录校验和流式历史校验。最终安装版在同一个快照上再次通过中文自然语言恢复，仍然识别出 STALE 状态。

## Agent 行为测试

设计了 16 个场景，覆盖正负触发、只报告的完成、推测、冲突、Git Diff、会话范围、敏感数据和历史注入。此次真实宿主运行覆盖显式初始化与采集、独立恢复、HEAD 漂移、中文自然触发、普通 Git 问答误触发，以及未经测试的用户报告。普通问答执行了 0 个工具命令。首次用户报告被记录为 REPORTED 状态条目，因缺少结构化 DONE 字段而没有触发完成警告。明确类型规则后，新会话保存为 REPORTED / DONE，并收到 `COMPLETION_WITHOUT_CHECKED_TEST`。其余场景属于设计、静态检查或 CLI 自动化覆盖，对应真实模型行为为 NOT VERIFIED。

## 已知限制与下一阶段

- Git 检查限显式根目录和常规本地仓库。链接工作树、嵌套仓库、外部对象和部分自定义配置会显示为 unknown。Git 证据校验范围是从当前 HEAD 可达的最近 20 个提交。
- 哈希可以证明当前证据字节与记录一致。测试是否覆盖结论、来源是否可信仍需独立判断。无 Git 项目依靠显式文件锚点发现变更。
- 冲突依靠声明键和值，去重依靠稳定 ID 与精确匹配。语义冲突和措辞差异需由 Agent 或人核对。
- 敏感信息检测属于启发式，存储为本地明文。可恢复性依赖文件系统和 ACL。历史不可达对象与旧快照没有自动清理。
- 下一阶段优先补齐 DSHarness 原生行为、剩余行为场景和真实项目复测，再扩充 Git 配置兼容性和受控的历史归档工具。

原始提示、未经改写的模型回复、事件轨迹、哈希清单和可重放项目均在独立演示与证据包中。

## 补充 · v1.1.x 与并发压测（2026-11-17）

v1.1.0 起：`next_steps` 从项目级 handoff 下沉到 `task` / `issue` 记录，新增 `restore --task SELECTOR`；v1.1.1 补 `records_with_steps` 索引；v1.1.2 把项目视角合并为 `project_note {summary, next_steps, scope}`。自动化测试由 37 项扩到 47 项，全部通过（`Ran 47 tests ... OK`）。

并发压测（新增 `tests/test_concurrent_capture.py`，5 用例）：两个与四个真实 python 进程同时 `capture` 同一项目，10 轮对撞每轮恰好一次提交、记录数与快照链各 +1；落败方一律安全失败（`WRITE_BUSY`，偶尔 `CAPTURE_CONFLICT`，均 exit 5），`validate` 保持 PASS，`snapshots/`、`records/` 每个对象 SHA-256 自校验一致，无 `.pending-*` 残留。探针 12 轮真实双进程的失败码为 `WRITE_BUSY` 12/12；`CAPTURE_CONFLICT` 用陈旧 `expected_capture_id` 单独用例固定。真实多任务恢复（A/B 各写下一步、只恢复 A）已在 DSH 与 Codex 两个宿主的新会话复验通过。

# 03–04 下一阶段的测试先行契约

状态：已写测试，工作台生产模块尚未实现。测试使用人工构造的资料，不访问真实知识库，不调用模型。

2026-10-22 阶段 B 补充：`test_offline_protocol.py` 的 12 个 Windows 协议测试通过，`executor/test_executor.py` 的 8 个 Linux 执行器测试通过，实际 WSL 离线交接与重启对账通过。此处原有 32 个审核契约仍 skipped，待阶段 C 实现。复现见 [阶段 B 说明](../../deploy/linux/README-stage-b.md)。

2026-10-22 验证结果：相关回归套件 **62 passed、32 skipped**，其中新增同步边界用例 **23 passed**。32 个跳过项均为尚未实现的工作台契约，不代表业务已通过验证。单独开启严格模式执行一个契约用例时，因缺少 `workbench.backend` 出现预期的 fixture setup error，确认缺失实现会阻止验收。

## 当前可运行的回归测试

项目 04 的 `tests/test_deeptutor_bridge.py` 已包含“首次建库后关联失败可以重试”的测试。新增 `tests/test_deeptutor_bridge_boundaries.py` 补充：

- 建库失败不登记归属，后续能重试。
- 同步超时/上游错误后释放锁，复用原知识库。
- 归属按后端地址、知识库名称和源目录隔离；只读、非空、未知知识库不被接管。
- 归属记录损坏时拒绝接管，不覆盖损坏文件。
- 索引中不能重复提交；同库并发只提交一次，不同库各自持锁。
- 提交索引不冒充索引完成；源 Markdown 内容不被同步适配器改写。
- 状态保留上游进度，未关联的库明确显示未关联；服务地址只接受本机地址。

## 待实现模块的边界

`test_review_workflow_contract.py` 约定未来的 `workbench.backend.services.review_workflow` 提供四个纯函数，输入输出使用 JSON 可表示的字典，业务校验失败抛出 `ValueError`；API 层以后再映射为 409/422 等响应。

```python
validate_document(document: dict) -> None
apply_review(document: dict, *, decision: str, reviewed_version: int,
             reviewed_sha256: str, reviewer: str, reviewed_at: str) -> dict
build_approved_manifest(project_id: str, documents: list[dict]) -> dict
validate_answer(answer: dict, manifest: dict, documents: list[dict]) -> None
```

- 来源卡需要 ID、项目、正整数版本、标题、作者、来源 URL、正文、artifact_id、正文 UTF-8 的 SHA-256、`content_level`（abstract/fulltext）及布尔型 `truncated`。可保存截断卡供补齐；本轮选定的小样本必须补齐摘要后才批准入库。这是测试提出的明确首版规则，不表示现有 04 已具备它。
- 审核只批准用户看到的确切版本和哈希；返回含审核记录的新字典，不修改入参。合法决策为 approved/rejected。
- 清单只收录目标项目已批准的资料，正文哈希与批准记录必须仍匹配。若批准已失效则拒绝构建。没有批准资料或出现重复 document_id 时拒绝构建。
- 清单字段：schema_version=1、project_id、documents、manifest_sha256。documents 每项只含 document_id、version、sha256、artifact_id、content_level，按 document_id 排序；哈希应基于稳定规范化后的清单内容，不包含时间和哈希字段自身。同样输入不能因为列表顺序不同生成不同清单。
- 回答的引用需对应清单中的文档、版本及哈希，提供能在原文核对的 quote。首版纯文本定位使用 `{kind: 'paragraph', value: 2}`，段落按空行分隔、忽略空段落、从 1 编号，quote 必须属于指定段落。摘要资料不能伪造 PDF 页码。全文 PDF 页码校验留到有真实解析结果后再扩展。
- evidence_status=grounded 必须至少一条有效引用；资料不足时可返回 insufficient 和空引用。校验结构与原文对应关系不等于证明模型的所有解释正确，更不能仅靠单元测试评价模型质量。

契约测试中没有用假生产函数返回预期结果。模块不存在时逐项显示 SKIPPED；模块存在但缺函数、缺依赖或行为错误时正常失败，不会掩盖未完成实现。

## 运行

在课程仓库根目录使用 PowerShell 7：

```powershell
$python = Join-Path (Get-Location) 'projects/project-04-Intelligence Gathering/.venv/Scripts/python.exe'
& $python -m pytest 'projects/project-04-Intelligence Gathering/tests/test_deeptutor_bridge.py' 'projects/project-04-Intelligence Gathering/tests/test_deeptutor_bridge_boundaries.py' 'projects/project-04-Intelligence Gathering/tests/test_api_limits.py' 'projects/project-04-Intelligence Gathering/tests/test_ai_response.py' 'workbench/tests/test_review_workflow_contract.py' -q -rs
```

开始实现工作台时，使用严格模式把缺失模块显式视为失败，完成后应全部通过且无 SKIPPED：

```powershell
$previousRequirement = $env:REQUIRE_RESEARCH_WORKFLOW
try {
    $env:REQUIRE_RESEARCH_WORKFLOW = '1'
    & $python -m pytest 'workbench/tests/test_review_workflow_contract.py' -q
} finally {
    $env:REQUIRE_RESEARCH_WORKFLOW = $previousRequirement
}
```

不在本轮单元测试中伪造覆盖的内容：真实 PDF 解析、跨进程任务恢复、真实嵌入/问答质量、资料外问题拒答效果，以及项目 02 的 GPU 实验。这些应在相应实现就绪后使用小样本集成验收。

2026-10-23 更新：审核服务和 ResearchPackage 已实现；`REQUIRE_RESEARCH_WORKFLOW=1` 下原有 32 项审核契约全部通过。工作台全部测试为 79 项与 17 个子测试通过，覆盖预算账本、适配器、输入冻结、产物防篡改、报告待审状态和 Windows 长路径。真实实验及容器证据另见 `deploy/linux/README-stages-c-f.md`，不包含在上述单元测试计数内。

2026-10-24 M0 更新：新增 `test_scientist02_journal.py`、`test_stage_result.py` 共 38 项，覆盖历史 journal 复放、阶段/图结构/哈希错误、不可覆盖重试、任务身份、三种状态与人工审核边界。工作台及 02 运行修复、最终写作反思合计 **123 passed、29 subtests passed、0 skipped**；Linux 执行器 8 项另行通过。见 [M0 实施记录](../../outputs/integration-m0-20261024/README.md)。运行旧 `unittest` 用例时，在当前测试进程设置 `tempfile.tempdir` 到工作区内目录，`--basetemp` 只覆盖 pytest 自身临时目录；不需要改系统设置。

2026-10-24 M1 更新：`test_workbench_m1.py` 新增 23 项，覆盖持久化、批准快照、并发幂等、Worker/重启/取消、实际 03 响应格式、失败索引只重新核验、确切版本检索及独立方案批准。本次与上述回归、04 连接边界合计 **208 passed、29 subtests passed、0 skipped**，Linux 8 项另行通过；真实本地索引/检索及浏览器 QA 另见 [M1 验收](../../outputs/integration-m1-20261024/README.md)。沙箱若卡在 Windows asyncio 的本机 socketpair 初始化，须通过受支持的权限机制运行，不修改事件循环或伪造通过。

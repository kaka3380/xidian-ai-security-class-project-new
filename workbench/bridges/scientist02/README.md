# 02 journal 与阶段结果桥接（M0）

工作台端仅使用标准库和既有 `executor.protocol`，不导入 02 的模型、GPU 或训练依赖。`reconstruct_journals()` 在 02 独立运行时才延迟导入真实 `Node/Journal`，返回原生 `overall_summarize(journals, cfg)` 所需的输入；调用付费总结须在后续任务与预算接线完成后进行。

- `freeze_journal_inputs()`：固定 `bfts_config.yaml`、四个明确的阶段目录及 journal 原始字节。当前只支持已验收的四阶段、journal v2；其他阶段配置显式拒绝，后续扩展须另行验收。
- `read_journal_inputs()`：核对固定哈希、完整阶段、节点 ID、父子关系、环、冲突与重复节点。阶段间沿用同一节点 ID 合法，不把它去重成新实验。
- `reconstruct_journals()`：按上游 v2 的 `node2parent` 重建。节点内空的 `parent_id/children` 是上游序列化行为；非空但冲突时拒绝。复制后调用会修改入参的 `Node.from_dict`，原始数据保留。
- `journal_evidence()`：逐节点保留代码哈希、上游指标、错误和 seed 标记。上游 `is_buggy=false` 不等于科研有效性核验通过，不选择或替换最佳节点。
- `replay()`：新建 attempt，复制固定输入、历史模型总结、原始 provenance 和错误日志，输出独立结果。已存在目录拒绝写入；失败后保留已复制产物和失败记录。再次执行使用新目录和 attempt，模型请求始终为 0。

分阶段结果使用 `workbench.stage-result` schema v1（`backend/services/stage_result.py`），与旧执行器 `result.json`、ResearchPackage v2 并存。新增协议不会自动让旧协议具备多文档或分阶段执行能力。

任务身份包含 `project_id/task_id/attempt/task_type/input_hash/parent_task_id`。结果分别携带执行、核验、人工审核状态及文件哈希。自动生产方始终写 `pending_review`；功能/离线证据不能标记为已批准科研结果。`journal_replay` 的 `passed` 仅指输入和图结构核验，`details.research_validation_status` 仍为 `pending`。

`summary/references/writeup/compile/auto_review` 当前仅冻结结果契约，尚未作为 Worker 可调度任务接通。取消确认、重启对账、服务端操作者记录、授权账本归属属于 M1–M3，不能由本模块的字段推定已经实现。哈希用于发现内容变化，不能防止同一系统用户同时改写文件和锁清单，也不替代批准记录。

实际离线复放、镜像与验证记录见 [M0 验收](../../../outputs/integration-m0-20261024/README.md)。

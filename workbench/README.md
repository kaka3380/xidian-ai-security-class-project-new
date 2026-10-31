# 本地研究工作台（真实小实验与基础报告）

工作台复用 04 的 React 组件与已有 Python 环境，独立持久化业务资料和任务，不合并 01–04 依赖。当前支持 M1 全部能力、提示注入专用实验契约、冻结回答分析/重新生成回答、独立指标核验、02 任务交接及 HTML/Markdown 基础报告。2026-10-26 已按用户批准的具体方案和 1 元新预算完成 24 次真实模型请求及报告交付，范围和结果边界见[课程作业报告](../submissions/26241215467/README.md)。科研审核仍待人工进行，论文链路未因此自动完成。

## 启动

仓库根目录，PowerShell 7：

```powershell
# 本批已构建前端；以后修改前端时再执行。
Push-Location -LiteralPath 'projects/project-04-Intelligence Gathering/webui'
try { & 'E:\nodejs\npm.cmd' run build } finally { Pop-Location }

# 启动独立 API 和单 Worker，窗口隐藏；已占用端口时拒绝重复启动。
.\workbench\start-workbench.ps1
```

入口：[研究工作台](http://127.0.0.1:8090/workbench)。04 启动后也可从侧栏或 `/workbench` 跳转。启动脚本默认使用项目 04 的 `.venv/Scripts/python.exe`，没有新增依赖；`-Foreground` 仅前台运行 API，需另开 PowerShell 执行 `python -m workbench.worker`（使用同一虚拟环境）。

索引和检索还需要 03 本机 API `127.0.0.1:8001` 及其既有本地嵌入服务。支持的 03 原启动脚本为 `projects/project-03-deeptutor/start-local.ps1 -NoBrowser`，它会启动 03 完整界面与 API。本次联调只启动已有本地嵌入脚本和 03 CLI `serve --host 127.0.0.1 --port 8001`；不启动收费模型。

## 文件与职责

- `backend/store.py`：SQLite 事务、资料/方案版本、审计、快照、幂等任务与三种状态；数据位于已忽略的 `workbench/data/`。
- `backend/api/app.py`：FastAPI 工厂 `create_app`，API 前缀 `/api/workbench`。只绑定本机，操作者来自服务器 OS 用户；禁止请求伪造审核人和审核时间。写请求要求同源和 `X-Workbench-Action: 1`，这是本机单操作者工作台，不是多用户登录系统。
- `worker.py`：OS 排他锁保证单 Worker；任务状态和心跳写 SQLite。重启只对账，不重新发送 03 创建请求或自动重跑阶段；数据库已提交、结果文件未写完的窗口可重建结果文件，不重新执行任务。
- `adapters/deeptutor.py`：沿用 04 的本机 HTTP 适配方式，通过 03 原生建库/上传/状态/原文接口，只传入批准快照列出的 5 份 Markdown。不使用整目录同步、旧固定库名或默认活动库。
- `bridges/deeptutor03/local_rag.py`：只在 03 独立解释器中执行，调用其原生 `rag_search`；固定本地嵌入目录选择、模型、维度、端点和检索配置，拒绝远端嵌入与额外模型重排。不会生成 LLM 答案或复制一套检索器。
- `backend/services/source_answer.py`：对检索片段逐条核对来源、版本、哈希与原文段落，保存可核对的摘录。字面证据不足则返回 `insufficient`；不是通用问题推理或语义充分性的证明，尤其可能保守拒绝跨语言问题。
- `import_sources.py`：把已有 04 来源卡导入为待审，不自动批准。`verify_m1.py` 的确认审核选项仅用于已有明确人类授权的确切清单，不能作为自动批准规则。
- 04 `ResearchWorkbench.tsx`：资料导入/新版本与审核、知识库版本和检索、方案、任务、结果五个页面；保留旧 04 页面和同步功能。

## 关键行为

1. 修改资料必须新建递增版本，新版本没有旧审核记录；拒绝/撤回后，新任务不能继续使用旧批准快照。历史快照和旧知识库保留。
2. 快照至少选择五份同项目、未截断、已批准的确切资料。文件内容和清单哈希核对后才提交 03；索引完成还要比较完整文件名集合和原文字节，之后才切换活动版本。
3. 检索必须显式指定 `kb_version` 与 `approved_manifest_hash`。默认拒绝陈旧版本；API 中显式开启 `allow_historical` 才能查看历史，并返回 `is_current=false`。问答和引用保存版本，界面不会把新版本标题套给旧版本引用。
4. 同项目幂等键只接受同一请求；SQLite 写事务下重复提交返回原任务。不同键也不能并发提交同一个待处理索引。重试分配新的任务 ID、attempt 和目录，保留旧失败/产物；同输入且已确认完成的自建索引只重新核验，不重复生成嵌入。
5. 取消排队任务立即结束；已经提交给 03 的任务只能请求取消并等待其报告终态，本版不强杀共享 03 服务。停止未确认时显示 `cancel_requested` 或 `interrupted`，并阻止后续重任务、重试和自动切库。
6. 方案独立存储和批准，绑定具体批准清单；关键配置或资料变化使旧批准无法生成新的冻结包。`package-preview` 只核对包，不提交任务；新实验须显式选择下述 profile，旧样例仅保留兼容预览。批准资料和 M1 索引不授予真实实验预算。
7. 执行、核验、人工审核状态分别保存；自动结果始终 `pending_review`。索引 `passed` 仅表示输入集合和文件核验通过，不等于科研指标或论文审核通过。

## 验收

真实本机链路、用户审核授权、离线故障/并发测试、UI 截图与限制见 [M1 验收记录](../outputs/integration-m1-20261024/README.md)。M0 原始锁清单和验收记录保留不变。

## 提示注入专用实验（前四步）

实施、基线归档和离线回归见 [基础验收记录](../outputs/integration-m2-foundation-20261026/README.md)。后续已恢复 Docker，并通过 [真实断网容器交接验收](../outputs/integration-m2-container-20261026/README.md)：API、Worker 重启对账、固定镜像、独立重算和结果台账均使用真实实现；输入为零预算合成冻结回答，不属于第五步真实模型实验。契约说明与请求示例见 [实验契约](experiment-contract.md)。本 profile 是固定方法基准，不进入上游四阶段搜索，不做调参、多 seed 聚合或无效消融；原 synthetic 样例仍有单独兼容路径，不作为通用实验默认值。

- `injection_experiment.py`：批准配置、冻结样本/回答与独立重算；三个指标必须齐全。无效回答保留在分母中；缺失回答直接核验失败。仅支持明确的 exact-answer / marker 判定，不把模型自评当正确标签。
- `executor02.py` → `worker.py` → 原 WSL 协议 → `container_tasks.py`：多份批准资料、独立 attempt、只读源码副本、确切模型和配置。冻结分析容器断网；重新生成只给受限预算 token，容器没有供应商密钥。旧样例的 `dataset.npz`、64 条预测、seed 42、1800 秒和四阶段次数不会进入新 profile。
- `experiment_budget.py`：方案单独授权、逐条请求内容哈希、持久化并发预留、调用次数及总额限制；重复批准同一方案返回原授权，重试共享原额度，失败或未知费用不退款。账本保留供应商原始回答，结果接收逐条比对，拒绝伪造请求 ID 或替换回答。
- 重启仅按原任务身份查询；提交确认丢失不重发，停止不明阻止重试和新重任务。已确认未提交的任务可新建 attempt；失败、取消和超时保留历史目录。

重新生成回答需要用户确认具体样本、方案及新预算。`prepare_small_experiment.py` 只保存待审材料；`run_small_experiment.py` 只在确切材料及费用获人类明确批准后执行，通过原 API、单 Worker 和受限容器完成。每个授权使用独立的 `model-sessions/{grant_id}` 运行目录，旧 `model-session/budget.sqlite` 不打开或结算；适配器使用的网关配置指针仅在本轮临时替换，结束后按原字节恢复。供应商密钥只由网关读取，容器只收到受限授权 token；网关在本轮完成后停止。历史共享 5 元不用于新 profile。

独立验证已有记录（不会重新执行实验）：

```powershell
& 'projects/project-04-Intelligence Gathering/.venv/Scripts/python.exe' -m workbench.verify_experiment '记录目录'
# 重新生成的回答还必须核对独立网关账本：
& 'projects/project-04-Intelligence Gathering/.venv/Scripts/python.exe' -m workbench.verify_experiment '记录目录' --data 'workbench/data'
```

所有自动结果继续 `pending_review`。构造的冻结样本只证明协议及计算行为；真实小样本也不证明一般鲁棒性、来源真实性或科研方法有效。第五步本次 0/12 攻击命中、12/12 干净任务正确只描述确切批准样本。

## 基础报告

结果页对“执行完成 / 独立核验通过”的提示注入任务显示“生成基础报告”。确定性导出器再次核对原始回答、计数、分母、源码、图表及独立预算账本，产出 HTML、Markdown、可重建 SVG、完整证据副本和逐文件哈希。报告不调用写作或审稿模型，不改变人工审核状态。旧 synthetic 报告仍使用原兼容路径。

```powershell
& 'projects/project-04-Intelligence Gathering/.venv/Scripts/python.exe' -m workbench.export_experiment_report '实验记录目录' --data 'workbench/data' --output '新的报告目录'
& 'projects/project-04-Intelligence Gathering/.venv/Scripts/python.exe' -m workbench.export_experiment_report '报告目录' --data 'workbench/data' --verify
```

`POST /api/workbench/projects/{project}/tasks/{task_id}/report` 返回本地报告链接；重复导出相同版本返回原报告，代码版本变化生成新报告 ID。报告中的攻击文本转义展示，页面不执行脚本；报告包不包含供应商密钥、网关凭据或原始凭据数据库。原始 SQLite 账本保留在工作台私有数据目录，报告携带去凭据的逐次账本快照。费用明确区分授权总额、保守预留和按实际 token 用量计算的高峰价格上界，不能把它们当作实际扣款。

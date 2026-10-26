# 本地研究工作台（M1）

工作台复用 04 的 React 组件与已有 Python 环境，独立持久化业务资料和任务，不合并 01–04 依赖。当前支持资料版本审核、批准快照、03 版本索引、来源摘录、研究方案独立批准、任务/结果台账及 M0 离线复放。真实实验、收费模型调用和论文任务尚未开放。

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
6. 方案独立存储和批准，绑定具体批准清单；关键配置或资料变化使旧批准无法生成新的冻结包。`package-preview` 只用于核对 ResearchPackage v2，`execution_enabled=false`。批准资料和 M1 索引不授予真实实验预算。
7. 执行、核验、人工审核状态分别保存；自动结果始终 `pending_review`。索引 `passed` 仅表示输入集合和文件核验通过，不等于科研指标或论文审核通过。

## 验收

真实本机链路、用户审核授权、离线故障/并发测试、UI 截图与限制见 [M1 验收记录](../outputs/integration-m1-20261024/README.md)。M0 原始锁清单和验收记录保留不变。

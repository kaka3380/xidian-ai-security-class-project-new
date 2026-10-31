# C–F 实施与验收记录

本记录续接 A/B，不覆盖历史验收证据。所有真实调用共用 `workbench/data/model-session/budget.sqlite` 的 5 元预算；费用字段为按供应商峰值单价核算的上界，不是账单金额。

## C：已实现

审核、资料版本与正文哈希校验、批准清单、独立方案批准、冻结 ResearchPackage 已实现。严格模式下原有 32 项审核契约全部通过。资料或方案变更会使旧批准失效。2026-10-23 工作台测试为 79 项通过、17 个子测试通过。

## D：真实实验

前三次尝试分别保留模型配置、预算预留策略和运行依赖问题的失败证据。第三次产物已校验并收回；代码重新生成数据且缺少约定的 metrics.json，科研验收失败，未将日志中的数字当成合格指标。

修复镜像补充 psutil 7.2.2；启动器只清理自身子进程，避免按关键词扫描并终止无关进程。冻结输入模板直接读取 `/task/dataset.npz`。v2 的数据、算法、40 轮、种子和时限与原方案相同，镜像与源码哈希变更单独记录。用户在展示 v2 后回复“继续”，记录于 `evidence/stage-d-v2-approval.json`。

第四次任务：`scientist02-synthetic-20261023-04`，2026-10-23 14:24–14:33（北京时间）正常退出，全部产物已校验收回。验收失败：最终产物未保存约定 metrics.json 和预测文件，且初始代码逐轮查看测试集，后续阶段生成额外数据/种子。详见 `evidence/stage-d-v2-acceptance.json`。结果已注册 pending_review，未用独立参考数字替代模型产物。累计 241 次调用的费用上界为 2.234858 元，包含历史失败与能力探测；后续调用仍须沿用同一账本。

```powershell
python -c "from workbench.offline_client import invoke; print(invoke('status','scientist02-synthetic-20261023-04','integration-demo'))"
python -c "from workbench.offline_client import collect_verified; print(collect_verified('scientist02-synthetic-20261023-04','integration-demo')['validation_status'])"
```

Windows 结果接收使用扩展路径，避免上游实验目录超过 260 字符而失败，不修改系统长路径设置。

## E：容器基础验证通过，完整接入待验收

官方镜像固定到 `registry-1.docker.io/tjbtech1/airesearcher@sha256:82ac65712eb504f100bff34448004004bb6e16fb6bf1e8f4cf8d37a60b0f8345`。实际 PyTorch 为 1.13.1+cu116，UID 1000，GPU 求和成功。已验证直接 TCP 入口、仅回环端口、4 CPU/4 GiB/256 PID、丢弃 capabilities 及停止确认，证据 `evidence/stage-e-tcp.json`。

这项基础验证没有调用模型，不能代替 01 主程序及同一执行器协议的端到端验收。主程序独立 CPU 环境已经锁定依赖，但 PyPI 下载多次超时，安装仍需确认；不得将创建了 .venv 目录视为安装完成。完整 researcher01 适配器及可选引擎入口尚未交付。

## F：草稿输出通过，TeX 编译受环境限制

`experiment_result.py` 对输入批准及产物哈希重新校验后注册 SQLite 记录，默认 pending_review。`experiment_report.py` 输出 HTML、TeX 和独立审核状态；`export-experiment-pdf.py` 输出 PDF。失败实验的缺失指标显示 Not available。

第三次失败记录的 PDF 已渲染为 PNG 并检查版式，路径 `outputs/stage-d-attempt-03/review.pdf`。TeX 项目位于 `outputs/stage-d-report-project`，应用内编译报 restricted-token sandbox 无法 enforce split writable root sets，未获得成功编译证据。PDF 来自 ReportLab；不能把它记为 TeX 编译成功。报告人工审核仍待进行。

第四次报告位于 `outputs/stage-d-attempt-04/index.html`、`review.pdf`，附 `methodology-review.json`；执行完成与科研验收失败分别展示。

## 运行恢复

Docker Desktop 4.77.0 的临时 AF_UNIX 套接字导致启动失败时，本轮停止失败进程，核实临时目录仅有零字节套接字后将整个目录改名备份，再启动。没有删除镜像、重置设置或清空数据。既有 sky-redis、yapi、yapi-mongo 按用户授权保持停止。

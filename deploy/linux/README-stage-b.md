# 阶段 B：离线交接

本阶段运行标准库模拟引擎，模型请求数为 0。Windows 使用参数数组调用 Ubuntu 执行器；无需启动 Docker。审核服务、SQLite Worker、真实 01/02 引擎、容器生命周期和计费控制在后续阶段实现。

## 文件与运行位置

- `executor/protocol.py`：稳定 JSON 哈希、路径约束和模拟任务包校验。
- `executor/main.py`：Linux 执行器和独立监督进程。
- `workbench/offline_client.py`：生成模拟包、固定 WSL 调用、核验并接收结果。
- `workbench/exchange/<task_id>/`：Windows 临时交接目录，已忽略 Git。
- `/home/research/research-runtime/runs/<task_id>/`：Linux 输入副本、状态、结果和产物。
- `workbench/data/experiments/<project_id>/<task_id>/`：Windows 最终包与结果，已忽略 Git。
- `examples/stage-b/`：本次通过验收的模拟包和输入样例。

任务 ID 全局唯一，项目 ID 同时参与归属校验；失败后以新 task_id 和递增 attempt 提交。参数不接受任意脚本、引擎入口或交换目录。

## 安装与运行

在仓库根目录使用 PowerShell 7。阶段 A 的 Linux Python 3.11 已存在；此执行器只使用标准库，不导入模型 SDK。部署时先结束现有模拟任务。

```powershell
.\deploy\linux\install-executor.ps1
python -m unittest workbench.tests.test_offline_protocol -v
wsl.exe -d Ubuntu-24.04 -u research --cd /mnt/e/work/ai-security-class-project --exec /home/research/research-runtime/engines/02/.venv/bin/python -m unittest executor.test_executor -v
python -m deploy.linux.verify-offline
```

默认集成验收终止它自己创建的监督进程，验证恢复后不自动重跑。要复现本次完整 WSL 重启验收，先确认 Ubuntu 中没有其他需要保留的运行任务，再执行：

```powershell
python -m deploy.linux.verify-offline --restart-wsl
```

该命令会终止并重新启动 Ubuntu-24.04，生成 7 个独立任务并更新 `evidence/stage-b-acceptance.json`。任务包和结果保留，便于复核。样例包记录当前源码哈希；更新执行器后应调用 `create_mock_package` 生成新样例。

单任务调用示例：

```powershell
python -c "from workbench.offline_client import invoke; print(invoke('probe'))"
python -c "from workbench.offline_client import create_mock_package,invoke; create_mock_package('demo-001'); print(invoke('submit','demo-001','offline-demo'))"
python -c "from workbench.offline_client import invoke; print(invoke('status','demo-001','offline-demo'))"
python -c "from workbench.offline_client import collect_verified; print(collect_verified('demo-001','offline-demo'))"
```

只有停止后的任务可 collect。长任务通过 `invoke('cancel', task_id, project_id)` 取消；返回结果确认原进程已停止。完成状态不会被后来的取消改写。

## 状态与恢复

提交前校验任务包、配置/方案/清单哈希、输入路径及字节哈希；只复制声明的输入，在 Linux 副本上再次校验。输入文件限 32 个、每个 1 MiB，模拟时长与总超时均最多 300 秒。

每个任务使用文件锁。写入采用临时文件、fsync 和原子替换；接收目录也原子发布。监督进程继承提交锁，自己登记 PID、进程启动计数和 Linux boot ID 后释放，覆盖启动与身份登记之间的崩溃窗口。mock 的工作就在该监督进程内执行，没有模型子进程或容器。

相同 ID 与包哈希返回原记录；不同哈希拒绝。监督进程独立计时；Windows 不轮询也能超时结束。重启后找不到对应进程则生成 interrupted 结果，保留原任务且禁止自动重放。已写 result、尚未写终态的窗口由 result 恢复。PID 复用或 boot ID 改变不能被当成原进程。

结果包含 execution_status 和 validation_status；退出码 0、指标缺失时验收失败。结果与每个产物的 SHA-256 在 Windows 落盘前重新校验，按项目/任务隔离。结果以 `pending_review` 和 `synthetic_protocol_test` 标记，模型请求为 0，未知费用为 null。

## 阶段边界

包中的审核人为 `offline-fixture`，仅代表合成测试数据。结构、版本与哈希校验不构成真实资料或方案批准，阶段 C 的 32 个审核契约仍未实现。真实 engine 名称会被拒绝。

当前进程恢复验收不能替代未来的容器标签对账、子进程树停止、CPU/内存/GPU 限额或外部模型预算验收。接入真实引擎时需单独完成这些内容。路径/项目校验用于本机协议隔离，不是多用户认证或抵御同一系统用户恶意修改运行目录的安全边界。

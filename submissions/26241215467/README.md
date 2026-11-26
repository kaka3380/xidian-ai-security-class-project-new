# 项目01–04课程复刻与联动作业

## 作业信息

- 学号 / 姓名：26241215467 / 张家康
- 项目编号：项目01、02、03、04
- 目标：搭建四项目独立运行环境，验证可执行的最小实例，实现资料审核、知识检索、实验执行和报告交付的联动工作台，并明确运行成功与科研结论有效之间的边界。

## 交付总览（2026-11-24）

| 模块 | 已有验证证据 | 待补验证 |
| --- | --- | --- |
| [01 · AI-Researcher](../../projects/project-01-ai-researcher/README.md) | Linux 容器基础设施、一次 TCP 执行验证和容器归属检查的离线对照。 | 完整自主研究流程待验收。 |
| [02 · AI-Scientist-v2](../../projects/project-02-ai-scientist-v2/README.md) | GPU、实验执行器、论文工具链以及单 seed 训练指标。内置样例的固定数据配置及阶段目标通过离线测试。 | 历史原版实验止于消融阶段。新配置的实际训练、采样对账及论文交付待验收。 |
| [03 · DeepTutor](../../projects/project-03-deeptutor/README.md) | 本地嵌入完成 5 份资料的索引，核对了引用与证据不足的返回。 | 更大样本的问答质量和跨语言检索待验证。 |
| [04 · Intelligence Gathering](../../projects/project-04-Intelligence%20Gathering/README.md) | 已实测 GitHub 公开资料采集、规则分析和学术知识库归档，另有 arXiv 离线复现。 | arXiv 实采遇到限流。04 自身的采集页面交互待验收。 |
| [联动工作台](../../workbench/README.md) | 已在同一项目内跑通本地索引、带出处检索、断网容器固定评测、独立核验和浏览器报告。 | 报告待人工审核。最新 GitHub Actions 和在线回答生成待验收。 |

课程源码整理时记录的上游提交为 01 `f9a6f8480860c193afff600eeffe3defcee8a978`、02 `96bd51617cfdbb494a9fc283af00fe090edfae48`、03 `f07029cfcf2c8dfccdb671cdfc343db8334f5741`。04 使用基于 MediaCrawler 的本地教学版，项目说明记录了来源和许可证。

## 修改内容

### 项目01：AI-Researcher

- 增加 WSL/Linux 部署、CPU 依赖锁定与源码快照脚本。
- 在 Docker 环境适配中增加任务标识、容器归属检查和局部清理边界。
- 完成容器基础设施与 TCP 执行最小验证；这不代表完整自主研究流程已验收。

2026-11-24 复现了研究流程使用的 `DockerEnv.stop_container()`。测试固定容器名 `course-demo`，用替身提供 Docker 状态和标签。旧式非托管路径按容器名直接请求停止；托管路径对不同任务的同名容器只查询归属，拒绝停止；对本任务的容器检查归属、请求停止并复查状态。从仓库根目录运行：

```powershell
python -m unittest discover -s projects/project-01-ai-researcher/tests -v
```

本机 Python 3.12.7 下 3 项通过。此处验证 Python 适配器发出的 Docker 命令和错误行为。实际容器与 TCP 的历史证据另见 [verification.json](verification.json)。

### 项目02：AI-Scientist-v2

- 增加 Python 3.11 / PyTorch CUDA 12.4 容器环境、依赖锁文件与 GPU 验证入口。
- 修正单 seed 配置、写作反思产物保留、缺失实验产物时提前失败，以及仅清理当前实验子进程等运行边界。
- 增加独立的 `workbench/start-scientist02-deepseek.ps1` 入口，复用已有 DeepSeek 网关；默认仅准备题目和小规模配置，显式 `-Run` 才执行收费实验。
- 支持自定义 ideas.json，配置 Matplotlib 可写缓存，独立保存实验目录和日志。
- 已有 GPU 运算、实验执行器和论文工具链的实测。最新原版科研实验产生真实训练指标，但日志止于消融阶段，不能宣称整轮成功或已完成论文。

### 项目03：DeepTutor

- 增加 Windows 本地 CPU 嵌入服务、可选依赖及启动/停止脚本。
- 修复模型诊断中解析配置与运行时配置类型的适配。
- 通过适配器接入原生知识库索引和检索，核对批准资料的版本、哈希与引用段落。
- 工作台问答为有出处的摘录与证据核验，不能等同于已验证的通用语义问答质量。本次没有复制其他同学示例中的 agentic_chat.yaml 修改。

### 项目04及研究工作台

- 增加 AI 响应结构校验，明确报告模型调用失败、解析失败及摘要/想法生成失败。
- 实现来源资料审核、批准快照、独立方案审核、幂等任务、单 Worker、取消与重启对账。
- 增加提示注入实验契约：冻结回答分析与显式授权后的回答生成分别处理，预算授权绑定具体方案和输入。
- 增加独立指标重算、证据哈希与防篡改核验、HTML/Markdown 报告以及人工待审状态。
- 增加工作台前端及与 DeepTutor 的本机联动、同步失败恢复和连接边界测试。

2026-11-24 用两条公开论文元数据构造 arXiv 响应。实际采集适配器发出 `all:Transformer` 查询，设置 `start=0`、`max_results=2`，按相关度降序。测试替身提供响应，适配器实际写入 JSONL。向输入追加一条重复记录后，用 `Transformer` 自定义主题做本地规则分析，3 条输入去重为 2 条分析记录，导出 2 条学术知识库笔记。逐层核对了第一条记录的标题、来源 URL、采集文件和 SQLite 文档 ID。 [Attention Is All You Need](https://arxiv.org/abs/1706.03762v7) · [BERT](https://arxiv.org/abs/1810.04805v2)

从仓库根目录使用项目04的解释器运行：

```powershell
$python = '.\projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe'
& $python -m pytest -q 'projects/project-04-Intelligence Gathering/tests/test_arxiv_archiving.py'
```

本机 Python 3.11.16 下 1 项通过。验证范围包括请求参数、响应解析、JSONL 写入、规则清洗、SQLite 记录及 Markdown 归档；HTTP 响应使用测试替身，模型调用为 0。

## 复现与验证

10 月课程验证使用 Windows 11、PowerShell 7、WSL2 / Ubuntu 24.04 和 Docker Desktop。项目02使用 Python 3.11.16、PyTorch 2.6.0+cu124 和 RTX 3060 Laptop 6GB。四项目各自使用独立依赖。

### 启动入口

```powershell
# 在课程仓库根目录执行；按各项目说明准备其独立依赖。
.\workbench\start-workbench.ps1
# 本机工作台：http://127.0.0.1:8090/workbench

# 项目03本机服务：
.\projects\project-03-deeptutor\start-local.ps1 -NoBrowser

# 项目02：默认只准备文件，不调用模型。
.\workbench\start-scientist02-deepseek.ps1
# 真实运行需用户自行配置项目外的密钥文件：
# .\workbench\start-scientist02-deepseek.ps1 -KeyFile 'C:\your-local-path\deepseek-key.txt' -Run
```

项目01和02的 Linux 环境准备见 [部署入口](../../deploy/linux/README.md)。在其他机器执行前，按[协作与环境配置](../../CONTRIBUTING.md)核对解释器、镜像和服务地址。

### 2026-11-24 验收摘要

本次复核的代码为 `2eab1e44494611dde405f2cc64e5f628c6d6829a`，环境为 Windows 11（构建 26200）、PowerShell 7.6.9、项目04的 Python 3.11.16。此前生成的本机验收摘要未记录各次运行的 Git 提交。原始验收文件的 SHA-256 和结构化结果见 [verification.json](verification.json)。

| 检查 | 复核结果 |
| --- | --- |
| 本次离线冒烟 | 2 项通过，生成的合成报告通过独立核验。使用测试替身，无需 Docker、GPU 或模型凭据。 |
| 本次已有报告复核 | 读取 11 月 24 日联动验收产物，再次核验报告通过，人工审核状态为 `pending_review`。 |
| 同日选定回归 | 96 项与 17 个子测试通过。1 项缺少历史日志跳过，8 项受 Windows 沙箱的本地套接字限制被排除。 |
| 同日本机联动验收 | 本地 Ollama 的 `qwen3-embedding:0.6b`（1024 维）完成 5 份资料的索引。样例问题核对到 1 条引用，范围外问题返回 `insufficient`。固定评测用冻结回答完成独立核验并导出报告，付费模型请求为 0。 |
| 同日 Docker 验收 | 两份验收记录均通过。实际容器使用固定镜像和断网网络模式，Worker 重启后成功对账，重复提交返回同一任务，各登记 1 份结果。预算及付费模型请求均为 0，人工审核状态为 `pending_review`。 |

从仓库根目录执行以下命令。冒烟输出需要一个新目录。已有报告目录可从本机联动验收摘要的 `report` 字段定位，验证时传入包含 `index.html` 的目录。

```powershell
$python = '.\projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe'
& $python -m workbench.verify_offline outputs/course-offline
& $python -m workbench.export_experiment_report '<已有报告目录>' --verify
```

真实 Docker 验收的环境准备和命令见 [工作台说明](../../workbench/README.md#提示注入专用实验前四步)。本次文档复核未重跑 Docker、DeepTutor 实际索引、前端构建或在线实验。

### 2026-11-24 工作流复跑

本轮基于 `d8a0139cd96687453c5878e14560346e1bf5e82b`，没有发起模型推理或使用付费预算。

| 环节 | 实际结果 |
| --- | --- |
| 本机回归与构建 | 离线冒烟 2 项通过。工作台和 04 回归 105 项及 17 个子测试通过，1 项缺历史归档跳过。01 独立测试 3 项、02 独立测试 2 项通过。项目04前端构建通过。 |
| 项目04公开采集 | 查询 `repo:pytorch/pytorch`，实际采集、清洗分析和归档各得到 1 条。核对了原始 URL、SQLite 文档 ID 和 Markdown 笔记。arXiv 同轮实采返回 HTTP 429，其模拟响应复现通过。 |
| 工作台固定评测 | Docker Desktop 29.3.1 的 Linux 容器使用本机固定镜像断网执行。重复提交返回同一任务，Worker 重启后成功对账，台账登记 1 份结果，独立重算通过。 |
| 浏览器报告 | 使用同一次固定评测产物，从结果页生成并打开报告。图表与 13 条本地证据链接均可用，未出现页面异常或移动端横向溢出。独立复核通过，状态为 `pending_review`。 |
| DeepTutor | 在隔离目录预检本机 Ollama 的 Qwen3 嵌入配置，确认回环地址及 1024 维。实际编码、索引和检索本轮待执行。 |
| 项目02原版 | 断网容器可见 RTX 5070 Ti（16,303 MiB）。本机缺项目02原版镜像及锁定 wheel 缓存，实际训练和论文未运行。 |

GitHub Actions 待首次远端运行。各次验收的产物路径和哈希见 [verification.json](verification.json)。实采来源见 [pytorch/pytorch](https://github.com/pytorch/pytorch)。

随后以 `6c5343109e416be4ea59047a7b359569b4bf00da` 复跑同一工作台项目的完整本机链路。隔离实例使用本机 Ollama 的 `qwen3-embedding:0.6b` 索引并核对了 5 份合成资料。范围内问题返回带原文支持的 `grounded`，范围外问题返回 `insufficient`。同一批准快照交给断网 Docker 处理冻结回答，独立重算通过。浏览器从同一任务导出报告，图表和 13 条证据链接通过检查，报告复核通过并处于 `pending_review`。付费模型请求为 0。

小组仓库推送返回 HTTP 403。该提交尚无 GitHub Actions 运行记录。

### 2026-10-27 提交前验证

- 工作台及04相关回归：247 passed、17 subtests passed，无跳过。
- 项目03本地嵌入与诊断测试：36 passed。
- 项目04前端：`npm run build` 通过，含 TypeScript 检查与 Vite 构建；存在非阻塞的包体积提示。
- DeepSeek 新入口：PowerShell 解析、默认准备、自定义题目、生成配置类型与 Docker 到网关的只读模型列表请求通过；测试没有调用真实模型生成。
- 10 月 27 日未重跑收费实验、实际知识库索引或浏览器验收。10 月 24 日和 26 日的集成结果另见 [verification.json](verification.json)。

后端回归示例（仓库根目录）：

```powershell
$python = '.\projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe'
& $python -m pytest workbench/tests `
  'projects/project-04-Intelligence Gathering/tests/test_deeptutor_bridge.py' `
  'projects/project-04-Intelligence Gathering/tests/test_deeptutor_bridge_boundaries.py' `
  'projects/project-04-Intelligence Gathering/tests/test_api_limits.py' `
  'projects/project-04-Intelligence Gathering/tests/test_ai_response.py' -q
```

### 最新原版项目02实验的结果边界

2026-10-27 的合成二分类实验使用 512 个样本、8 个特征、16 宽隐藏层、10 个 epoch、单 seed；测试集 103 个样本。对保存预测独立重算，按最佳验证损失选取模型后：

| weight_decay | 测试正确数 | 测试准确率 |
| --- | --- | --- |
| 0 | 91/103 | 88.35% |
| 0.001 | 92/103 | 89.32% |
| 0.01 | 92/103 | 89.32% |

这只说明历史小样本指标与保存预测相符。不能将最后一轮的 92.23% 与最佳验证模型的指标混用，也不能据单 seed 和一个样本的差异证明方法优越性。当时样例禁止网络数据下载，而上游基线调优和创新研究阶段要求引入 Hugging Face 数据集，各组采样顺序也未严格固定。

2026-11-24 为内置样例启用 `experiment.fixed_local_data`，让基线调优和创新研究重用固定本地数据。准备步骤要求各权重衰减组复用同一份逐 epoch 样本顺序，并保存划分和批次索引供复核。自定义题目和原有配置继续使用原阶段目标。实际运行要用包含本次源码的项目02镜像，构建见[部署说明](../../deploy/linux/README.md#构建与验收)。

运行 `python -m unittest discover -s projects/project-02-ai-scientist-v2/tests -v` 可对照固定数据与默认阶段目标。本机 Python 3.11.16 下 2 项通过，内置和自定义准备配置的离线检查也通过。项目02日志回归 20 项通过，1 项缺历史日志跳过。Docker 引擎本次不可用，样本顺序的实际复用仍需按训练产物核验。现有小样例入口跳过写作和审稿，完整论文流程需单独验收。

2026-10-26 的提示注入专用实验是另一条已冻结输入的小实验：24 个响应有效、攻击成功 0/12、干净任务正确 12/12，指标已独立重算。它不代表上述原版科研实验通过，也不能推广为通用提示注入防护结论。实际供应商账单没有独立获取，网关预算记录不冒充平台实扣费用。

## 提交检查

- [x] 修改属于本人项目01–04作业和联动工作台范围。
- [x] 不提交 API Key、Cookie、运行数据库、浏览器状态、虚拟环境或原始运行日志；学号与姓名用于课程署名。
- [x] 保留上游版权与许可证文件。
- [x] 区分本次测试、历史实测、未运行部分及已知问题；自动核验不替代人工科研审核。

# 项目01–04课程复刻与联动作业

## 作业信息

- 学号 / 姓名：26241215467 / 张家康
- 项目编号：项目01、02、03、04
- 目标：搭建四项目独立运行环境，验证可执行的最小实例，实现资料审核、知识检索、实验执行和报告交付的联动工作台，并明确运行成功与科研结论有效之间的边界。

## 修改内容

### 项目01：AI-Researcher

- 增加 WSL/Linux 部署、CPU 依赖锁定与源码快照脚本。
- 在 Docker 环境适配中增加任务标识、容器归属检查和局部清理边界。
- 完成容器基础设施与 TCP 执行最小验证；这不代表完整自主研究流程已验收。

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

## 复现与验证

环境为 Windows 11、PowerShell 7、WSL2 / Ubuntu 24.04、Docker Desktop。项目02使用 Python 3.11.16、PyTorch 2.6.0+cu124；GPU 为 RTX 3060 Laptop 6GB。四项目保留独立依赖，不要求安装进同一个虚拟环境。

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

项目01和02的 Linux 环境准备见 [部署入口](../../deploy/linux/README.md)。这些脚本包含本机路径或镜像标识，其他机器使用前需核对，不构成跨机器零配置安装保证。

### 本次提交前验证（2026-10-27）

- 工作台及04相关回归：247 passed、17 subtests passed，无跳过。
- 项目03本地嵌入与诊断测试：36 passed。
- 项目04前端：`npm run build` 通过，含 TypeScript 检查与 Vite 构建；存在非阻塞的包体积提示。
- DeepSeek 新入口：PowerShell 解析、默认准备、自定义题目、生成配置类型与 Docker 到网关的只读模型列表请求通过；测试没有调用真实模型生成。
- 本次未重跑真实收费实验、真实知识库索引或浏览器验收。2026-10-24/26 的历史集成记录与本次回归分别记载，摘要见 [verification.json](verification.json)。

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

这只说明本次小样本指标与保存预测相符。不能将最后一轮的 92.23% 与最佳验证模型的指标混用，也不能据单 seed 和一个样本的差异证明方法优越性。样例禁止网络数据下载，而上游部分阶段要求新增 HuggingFace 数据集，存在阶段目标冲突；各组采样顺序也未严格固定。这些问题尚未在本次 PR 中修正，最新整轮科研实验未标为验收通过。

2026-10-26 的提示注入专用实验是另一条已冻结输入的小实验：24 个响应有效、攻击成功 0/12、干净任务正确 12/12，指标已独立重算。它不代表上述原版科研实验通过，也不能推广为通用提示注入防护结论。实际供应商账单没有独立获取，网关预算记录不冒充平台实扣费用。

## 提交检查

- [x] 修改属于本人项目01–04作业和联动工作台范围。
- [x] 不提交 API Key、Cookie、运行数据库、浏览器状态、虚拟环境或原始运行日志；学号与姓名用于课程署名。
- [x] 保留上游版权与许可证文件。
- [x] 区分本次测试、历史实测、未运行部分及已知问题；自动核验不替代人工科研审核。

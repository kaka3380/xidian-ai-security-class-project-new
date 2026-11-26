# 协作开发与环境配置

本仓库包含四个独立项目和联动工作台。课程要求见 [README.md](README.md)，AI 开发约定见 [AGENTS.md](AGENTS.md)。请按本次任务选择环境，无需一次安装全部项目。

## 分支与提交

`origin` 指向小组仓库 `kaka3380/xidian-ai-security-class-project-new`。老师仓库为 `CDJDAA/xidian-ai-security-class-project-new`；仅在需要同步课程基础代码时添加 `upstream`，先用 `git remote -v` 检查。

在仓库根目录操作，先确认工作区没有需要保留的未提交改动：

```bash
git status
git switch main
git pull --ff-only origin main
git switch -c students/YOUR_STUDENT_ID/project-04
```

替换学号和项目编号；小组内部独立任务可采用 `feat/描述` 或 `fix/描述`，向小组 `main` 提 PR。向老师提交作业时仍遵循课程分支、PR 标题和模板要求。不要在项目子目录重新初始化 Git。

- 每次提交聚焦一项改动，提交前检查暂存差异；只暂存本次相关文件。
- 推送自己的分支，通过 PR 合并；PR 写明目标、改动、环境、验证命令及结果、已知限制。
- 不强推共享分支、不覆盖他人改动。评阅期间不重写作业提交历史。
- 使用 UTF-8，避免整文件换行符变更；保留上游版权与许可证。

## 环境选择

| 模块 | 开发环境与入口 |
| --- | --- |
| 项目01 | Python 3.11、Docker；推荐 Linux / WSL2，见[项目说明](projects/project-01-ai-researcher/README.md) |
| 项目02 | Linux / WSL2、Python 3.11、NVIDIA GPU / CUDA，见[项目说明](projects/project-02-ai-scientist-v2/README.md)和[部署说明](deploy/linux/README.md) |
| 项目03 | 独立 Python 环境、Node.js；源码安装及初始化见[项目说明](projects/project-03-deeptutor/README.md) |
| 项目04 | 建议 Python 3.11、Node.js 22、uv；见下方最小启动步骤 |
| 工作台 | 复用项目04环境和前端；检索功能还需项目03服务，容器实验需 Linux 执行器 |

不同操作系统分别重建环境，不复制 `.venv/`、`node_modules/` 或模型缓存。Python 使用各项目的解释器；Node 依赖在各自前端目录安装。已有锁文件优先使用，更新依赖时单独说明原因并提交对应锁文件。

### 项目04：最小启动

以下命令可在 PowerShell 或 Bash 中执行，从仓库根目录开始：

```bash
cd "projects/project-04-Intelligence Gathering"
uv sync --frozen
uv run start.py
```

默认访问 `http://127.0.0.1:8080`。需要 Playwright 浏览器采集时另执行 `uv run playwright install chromium`。无模型密钥时先验证本地清洗、去重等功能。首次启动会从 `.env.example` 创建本地 `.env`，模型与知识库设置见[项目04说明](projects/project-04-Intelligence%20Gathering/README.md)。

修改项目04或工作台前端时，在项目04的 `webui/` 中执行：

```bash
npm ci
npm run build
```

Vite 开发代理默认连接项目04的 `http://localhost:8080`。自定义后端端口时，在启动 `npm run dev` 的终端设置 `DEV_API_ORIGIN`，或在 `webui/.env.local` 里写入如 `DEV_API_ORIGIN=http://127.0.0.1:8081`。API 和工作台跳转使用同一个后端地址。

项目04的 Python 锁来自清华 PyPI 镜像，前端锁包含 npmmirror 归档地址。日常安装用 `uv sync --frozen` 与 `npm ci`。团队换源时，在独立 PR 中修改 Python 索引并重建 `uv.lock`，用新 registry 重新生成前端锁，检查版本与完整性摘要的差异，再跑两平台安装和构建。已有锁的下载地址不会随运行时模型配置变化。

### 工作台：Windows 与 Linux / macOS

Windows 使用 PowerShell 7，从仓库根目录执行：

```powershell
.\workbench\start-workbench.ps1
```

脚本要求项目04已建立 `.venv`，默认后台启动 API 与单 Worker，入口为 `http://127.0.0.1:8090/workbench`。

Linux / macOS 可在仓库根目录的两个终端分别执行原生 Python 入口；先完成项目04依赖安装和前端构建：

```bash
# 终端一：API
"projects/project-04-Intelligence Gathering/.venv/bin/python" -m uvicorn workbench.backend.api.app:create_app --factory --host 127.0.0.1 --port 8090
# 终端二：Worker
"projects/project-04-Intelligence Gathering/.venv/bin/python" -m workbench.worker
```

这些命令只提供工作台进程入口，不代表所有联动功能已跨平台验证。知识索引与检索需要项目03的 `127.0.0.1:8001` API 及本地嵌入服务；实验还需独立配置执行器、镜像和网关，详见[工作台说明](workbench/README.md)。同一数据目录只运行一个 Worker，不要同时使用启动脚本和手动入口。

## 本地配置与路径

| 配置 | 读取方与默认值 |
| --- | --- |
| `DEEPTUTOR_API_URL` | 工作台 API、Worker 和项目04，默认 `http://127.0.0.1:8001` |
| `DEEPTUTOR_PYTHON` | 工作台 API、Worker，默认项目03自己的平台虚拟环境。自定义时推荐绝对路径 |
| `WORKBENCH_URL` | 项目04的侧栏入口，默认 `http://127.0.0.1:8090`。改变工作台监听端口时同步修改 |

启动工作台前在同一终端设置环境变量，让后台 API 和 Worker 继承同一配置。项目04也可以在自己的 `.env` 里设置两个服务地址。地址只接受本机 HTTP origin，不含路径。Windows 启动脚本支持 `-Port` 和 `-Python`。手动启动时通过 uvicorn 的 `--port` 选择端口。

- 项目01使用其 `.env.template`；项目02按 README 配置模型环境变量和实验 YAML；项目04使用其 `.env`。不要在根目录创建一份配置期待所有项目共同读取。
- 项目03使用初始化向导或网页设置，配置保存在其运行主目录的 `data/user/settings/`，**不读取项目根目录 `.env`**。
- 密钥、Cookie、浏览器登录状态、知识库、运行数据库和日志留在本机。新增配置提供不含真实值的示例；提交前核对 `git status` 与 `git diff --cached`，不要只依赖忽略规则。
- 文档中的 `E:\...`、`/mnt/e/...`、镜像标识及“本机已安装”描述来自旧环境。迁移时检查脚本内的路径和服务地址；新增可复用配置使用参数或环境变量，默认值从仓库位置推导。
- Windows 路径传给 WSL 前转换成实际挂载路径；包含空格的项目04路径始终加引号。不要直接复用他人的运行数据库、批准记录或实验预算。

## 验证与交接

先运行受影响模块的离线测试。工作台回归可在仓库根目录执行：

```powershell
# Windows PowerShell
& '.\projects\project-04-Intelligence Gathering\.venv\Scripts\python.exe' -m pytest workbench/tests -q
```

```bash
# Linux / macOS
"projects/project-04-Intelligence Gathering/.venv/bin/python" -m pytest workbench/tests -q
```

如环境缺少 pytest，先在该开发环境安装；各项目其他检查遵循自身文档。前端改动执行对应目录的构建，涉及联动时补充实际服务验证。运行真实模型或实验前明确输入、方案和预算。

根仓库 CI 在 Windows 和 Linux 执行离线用例，在 Linux 构建项目04前端。项目04附带已构建的 `api/webui/` 供直接启动，前端功能修改应在同一提交更新对应产物，纯后端改动无需刷新产物。需要 GPU 或已下载嵌入模型的检查由组员在配置好的机器执行，见[部署说明](deploy/linux/README.md)和[工作台验收](workbench/README.md#验收)。

交接记录写清操作系统、Python / Node 版本、必要的 GPU / CUDA 信息、启动目录、配置项名称（不含秘密）、验证命令及结果。区分本次运行、历史记录与未验证部分；本仓库不保证旧机器脚本在新机器上零配置运行。

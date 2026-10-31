# 项目 04：Intelligence Gathering使用指南

[课程任务](COURSE.md) · [课程总说明](../../README.md) · [整理前说明](README.original.md)

面向人工智能安全研究的本地科研辅助平台，串联多源采集、内容分析、知识库归档、研究想法生成及外部实验任务管理。基于 MediaCrawler 二次开发，保留原作者版权与非商业学习许可证，详见 [LICENSE](LICENSE)。

## 与项目03联通

04主页面侧栏已增加[学习问答](http://127.0.0.1:8080/?view=learning)，将学术归档和社交/分析归档分别同步到03的 `ig04-academic`、`ig04-social` 知识库，再在03中选择对应资料问答。支持新增/修改同步，重复点击不重复导入；03和本地嵌入服务需先启动。旧的 `/deeptutor` 地址会跳转到新栏。首次建库后若关联失败，可直接重试；仅允许恢复本适配器创建且仍为空的库，归属记录保存在已忽略的 `data/deeptutor/created-knowledge-bases.json`。工作台联动使用方式见[工作台说明](../../workbench/README.md)。

## 功能与使用条件

| 功能 | 所需配置 |
| --- | --- |
| Web 界面、文件管理、清洗去重、关键词提取 | Python 依赖即可，附带已构建前端 |
| GitHub / arXiv 检索 | 网络连接；GitHub Token 可选 |
| 社交平台采集 | 本机 Chrome/Edge 或 Playwright 浏览器；按平台完成本人账号登录 |
| AI 内容摘要与 Idea 生成 | OpenAI 兼容模型服务的地址、模型名称和 API Key |
| 双知识库同步 | 本地可写目录；无需安装 Obsidian 即可生成 Markdown |
| 自动化实验 | 另行安装兼容的 AI Scientist 框架、配置实验环境与基础项目 |

## 快速开始

建议 Python 3.11、Node.js 22。即使使用附带网页，部分采集平台的 JavaScript 签名仍需要 Node.js。使用 JSONL 存储时无需安装 MySQL、Redis 或 MongoDB。

从课程仓库根目录进入本项目，再执行启动命令：

```powershell
cd "projects/project-04-Intelligence Gathering"
```

```powershell
uv sync --frozen
uv run playwright install chromium
uv run start.py
```

首次启动自动从 `.env.example` 创建 `.env`，并建立演示知识库目录。浏览器打开 http://127.0.0.1:8080 。按 Ctrl+C 停止服务，端口冲突时使用 `uv run start.py --port 8081`。

没有 uv 时，可使用标准 Python 环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe start.py
```

macOS / Linux 将 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。锁文件是首选依赖来源；requirements.txt 提供同一组直接依赖，间接依赖仍由 pip 解析。

## 配置模型和知识库

编辑本项目目录的 `.env`，修改后重启服务：

```dotenv
AI_API_KEY=填写自己的密钥
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
GITHUB_TOKEN=
OBSIDIAN_ACADEMIC_VAULT_PATH=knowledge/academic
OBSIDIAN_SOCIAL_VAULT_PATH=knowledge/social
OBSIDIAN_VAULT_PATH=knowledge/academic
```

API Key 留空时仍可进行本地清洗、去重与关键词提取；模型生成能力需有效服务配置。GitHub Token 用于已获授权的 API 请求，勿提交到仓库。自定义知识库应填写实际可写路径；学术库必须预先存在 `30-来源/自动采集` 目录，以保护用户已有结构。

## 一次完整的教学演示

1. 新建采集任务，先选择 GitHub 或 arXiv，输入 `adversarial examples`、`RAG security` 等检索词，设置较小采集数量；保存格式选 JSONL。
2. 在采集结果中查看原始记录，选择文件进行内容分析。学术平台优先使用英文关键词，以减少漏检。
3. 打开知识库查看归档结果。必要时点击同步；只有已有数据时才会出现对应分类。
4. 配置模型后，基于分析结果生成候选研究想法，人工核验来源与证据。
5. 如已安装兼容实验框架，选择 Idea 与匹配的基础项目，通过环境预检后启动实验。

社交平台可能要求扫码或额外验证，需要用户在弹出的浏览器中完成。评论是原始讨论，不等同于研究结论；模型输出也应人工核验。

## 文件保存位置

- 原始采集：`data/<平台>/jsonl/`，正文与评论分别保存。
- 分析数据：`data/analysis/`；合并输入位于其中的 `batch_inputs/`。
- Idea：`data/scientist/ideas/`。
- 学术归档：`knowledge/academic/30-来源/自动采集/<主题>/<平台>/`。
- 社交正文：`knowledge/social/10-原始内容/<主题>/<平台>/`。
- 评论与证据：`knowledge/social/20-评论与证据/<主题>/<平台>/<条目>/`。
- 分析报告：`knowledge/social/30-专题观察/分析报告/<主题>/`。

当前学术知识库同步基于已分析入库的记录，不保证所有原始采集条目均归档。原始 JSONL 保留于 data 目录。主题分类为规则映射，不代表人工审定。

## AI Scientist 可选接入

本仓库提供实验管理适配层，不附带外部框架、模型权重或 GPU 环境。配置：

```dotenv
AI_SCIENTIST_ROOT=/实际路径/兼容框架
AI_SCIENTIST_PYTHON=/实际路径/兼容框架/.venv/bin/python
```

Windows 下 Python 路径一般为 `.venv/Scripts/python.exe`。当前适配层要求 `launch_scientist_bfts.py`、对应配置与 `research_platform` 数据结构，不能假设任意上游版本克隆后即可直接接入。课程中的 project-02-ai-scientist-v2 是独立上游项目，放在同一目录下不表示已经与本项目适配。未安装时，基础采集与分析可用，实验预检会说明缺项。具体接口检查见 `api/routers/scientist.py` 的 `_preflight` 与 `_build_command`。不要为未验证的环境宣称实验已复现。

## 前端开发

```powershell
cd webui
npm ci
npm run dev
```

开发地址 http://127.0.0.1:5173 ，代理后端 8080。重新构建执行 `npm run build`，产物写入 `api/webui/`；附带全部 Vite、TypeScript 和包管理配置。

## 常见问题

- 首页只有 API 信息：进入 webui 安装依赖并运行构建，再刷新页面。
- 模块缺失：确认安装依赖与启动服务使用同一个 Python 环境。
- 登录失败：检查浏览器中的登录/验证提示，重新登录；不要公开 Cookie。
- 知识库为空：先采集并分析，再确认路径和写权限；查看同步返回结果。
- 词云字体缺失：`config/base_config.py` 中 FONT_PATH 需指向自己有权使用的中文字体，本包未分发原项目字体文件。
- 实验预检失败：按缺项配置外部框架，不影响基础模块教学。

## 发布到 GitHub

本项目是课程仓库的第 04 个项目。请在课程仓库根目录使用统一 Git 仓库，在个人分支提交修改；不要在本子目录重复初始化 Git。课程流程见 [总说明](../../README.md)，项目任务见 [COURSE.md](COURSE.md)。`.gitignore` 已排除 `.env`、账号浏览器目录、生成数据、知识库及虚拟环境。上传前查看 `git status --short`，保留 LICENSE 和原始版权说明。本地服务具有文件及任务管理能力，默认只监听 127.0.0.1，不应直接作为公共互联网服务部署。

## 来源与许可

- MediaCrawler：https://github.com/NanmiCoder/MediaCrawler ，采集基础及部分 WebUI，遵循本仓库保留的 NON-COMMERCIAL LEARNING LICENSE 1.1。
- Sakana AI Scientist：https://github.com/SakanaAI/AI-Scientist ，科研自动化相关工作；外部框架的具体版本、兼容性与许可需单独确认。
- FastAPI、React、Vite、Playwright 等依赖分别适用各自许可证。

本项目用于非商业学习研究。对外发布时请明确二次开发关系，遵守平台访问规则及原项目许可。

## 新同学首次使用顺序

1. 安装 Python 3.11、Node.js 22 和 uv；分别检查 `python --version`、`node --version`、`uv --version`。没有 uv 时使用上文的 pip 安装方案。
2. 按“快速开始”进入本项目目录并安装依赖。四个课程项目分别使用自己的环境。
3. 首先在不配置模型 Key 的情况下启动，确认 http://127.0.0.1:8080 能打开。模型摘要和 Idea 生成此时不可用。
4. 在本项目目录运行 `uv run configure_api.py`，填写自己的模型地址、模型名和密钥，然后重启服务。具体见 [教学 API 配置](教学API配置说明.md)。
5. 从 GitHub/arXiv 小规模检索开始，选择 JSONL 存储；有数据后再运行分析、归档及模型生成。先核验一条来源和结果，再扩大范围。
6. 按 Ctrl+C 停止服务。以后从同一项目目录执行 `uv run start.py` 即可，无需每次重新安装。

## 如何判断演示完成

- 网页可访问，所选采集任务产生实际记录，而非只有“启动成功”提示。
- 分析结果与原始内容对应，知识库目录中能找到对应归档。
- 模型功能启用时，核验摘要和研究想法是否有来源支撑；将生成结论与人工确认结果区分。
- 保存运行步骤和不含密钥的截图/报告，按 COURSE.md 提交修改。

`.env`、`data/`、`knowledge/` 和浏览器状态仅留在本机；不要把整个运行目录压缩后直接上传。自动化实验是可选模块，未安装兼容实验框架时不影响基础采集与分析。

## 本说明的验证范围

使用步骤依据当前项目 README、配置与启动入口整理。本次未重新安装依赖、启动服务或验证外部模型/采集平台可用性。各平台访问条件和模型权限以实际运行结果为准。

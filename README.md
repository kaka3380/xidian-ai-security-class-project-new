# 人工智能安全课程基础项目

班级仓库：[CDJDAA/xidian-ai-security-class-project-new](https://github.com/CDJDAA/xidian-ai-security-class-project-new)。

本课程目录包含四个基础项目，供同学选择开展源码阅读、复现、功能扩展与安全分析。前三个来自 GitHub 上游源码，第四个为本地整理的“Intelligence Gathering”。各项目独立安装和运行，共用课程仓库的分支与提交规范。

## 项目选择

| 编号 | 项目 | 主要内容 | 项目说明 | 课程任务 |
| --- | --- | --- | --- | --- |
| 01 | AI-Researcher（HKUDS） | 自动研究流程与研究代理 | [README](projects/project-01-ai-researcher/README.md) | [COURSE](projects/project-01-ai-researcher/COURSE.md) |
| 02 | AI-Scientist-v2（SakanaAI） | 研究想法生成、实验树搜索与论文生成 | [README](projects/project-02-ai-scientist-v2/README.md) | [COURSE](projects/project-02-ai-scientist-v2/COURSE.md) |
| 03 | DeepTutor（HKUDS） | 智能辅导、知识问答与学习代理 | [README](projects/project-03-deeptutor/README.md) | [COURSE](projects/project-03-deeptutor/COURSE.md) |
| 04 | Intelligence Gathering | 多源采集、内容分析、知识库归档与研究想法生成 | [README](projects/project-04-Intelligence%20Gathering/README.md) | [COURSE](projects/project-04-Intelligence%20Gathering/COURSE.md) |

## 目录结构

```text
xidian-ai-security-class-project/
├── README.md
├── .gitignore
└── projects/
    ├── project-01-ai-researcher/
    ├── project-02-ai-scientist-v2/
    ├── project-03-deeptutor/
    └── project-04-Intelligence Gathering/
```

每个项目都包含中文使用 README.md 和课程任务 COURSE.md。README 按环境准备、依赖安装、模型配置、启动、首次演示与排错组织；整理前的说明保存在各项目 README.original.md。各自的依赖、示例与原始许可文件保存在对应目录内。

## 开始使用

小组联动开发见 [协作与环境配置](CONTRIBUTING.md) 和 [研究工作台](workbench/README.md)。

1. 选择一个项目，阅读其 README 与 COURSE.md，再进入该项目目录执行安装和启动命令。
2. 四个项目分别创建环境，按各自要求配置模型服务与 API Key。
3. 先运行小规模示例，记录环境和结果，再修改代码；本次目录整理不代表已完成运行验证。
4. Intelligence Gathering 适合从本地资料处理与知识归档流程入手；基础启动见 [项目 04 快速开始](projects/project-04-Intelligence%20Gathering/README.md#快速开始)。
5. AI-Scientist-v2 上游要求 Linux、NVIDIA GPU、CUDA 和 PyTorch，并会执行模型生成代码，应遵循其隔离运行说明。

项目 02 与项目 04 是独立项目。Intelligence Gathering 的可选实验适配层要求特定接口，不能因两者放在同一课程目录就认为已经互相兼容。

## 协作方式：Fork → 个人分支 → 推送 → PR

本课程统一采用 Fork 方式：每位同学在自己的 GitHub 账号下保存和修改项目，通过 Pull Request（PR）向班级仓库提交作业。**不需要教师添加协作者，也不需要接受仓库写入邀请。** 各自的修改不会影响其他同学或班级基础代码。

### 1. Fork 班级仓库

登录自己的 GitHub 账号，点击本仓库右上角 **Fork → Create fork**，在自己的账号下建立副本。

### 2. 下载自己的副本

在电脑安装 Git，将下面的 `YOUR_USERNAME` 替换为自己的 GitHub 用户名。若 Fork 时改了仓库名，也要替换对应名称：

```bash
git clone https://github.com/YOUR_USERNAME/xidian-ai-security-class-project-new.git
cd xidian-ai-security-class-project-new
git remote -v
```

确认 `origin` 指向**自己的账号**，而不是 `CDJDAA`。合集包含较大的上游示例和演示资源，首次下载可能较慢。

### 3. 创建个人作业分支

在课程仓库根目录执行，将 `YOUR_STUDENT_ID` 替换为学号；以下以项目 04 为例：

```bash
git switch main
git switch -c students/YOUR_STUDENT_ID/project-04
```

进入所选项目目录，按照该项目 README 安装、配置并运行。其他项目替换编号和目录即可，不要在项目子目录重新初始化 Git。

首次使用 Git 时，设置自己的姓名和 GitHub 已验证邮箱（或 GitHub 提供的隐私邮箱）：

```bash
git config user.name "自己的姓名或GitHub用户名"
git config user.email "自己的GitHub邮箱"
```

### 4. 分次提交并推送到自己的仓库

每完成一个功能或修复一个问题，回到课程仓库根目录执行：

```bash
git add "projects/project-04-Intelligence Gathering"
git diff --cached
git commit -m "项目04：说明本次具体改动"
git push -u origin students/YOUR_STUDENT_ID/project-04
```

首次推送后，继续在同一分支修改、commit，再执行 `git push` 即可。提交前检查差异，勿加入密钥、Cookie、个人数据、依赖环境或日志。Git 只记录已提交的修改，不会自动记录每次保存文件。

### 5. 向班级仓库创建 PR

在自己的 Fork 中选择作业分支，点击 **Contribute → Open pull request**；也可从班级仓库的 **Pull requests → New pull request → compare across forks** 进入。

确认比较方向：

| 字段 | 应选择的内容 |
| --- | --- |
| base repository（目标仓库） | `CDJDAA/xidian-ai-security-class-project-new` |
| base（目标分支） | `main` |
| head repository（来源仓库） | 自己账号下的 Fork |
| compare（来源分支） | `students/自己的学号/project-04` |

PR 标题为 `[项目编号] 学号 - 姓名`，按自动显示的作业模板填写。**第一次有实际改动并推送后，就创建草稿 PR（Draft pull request）**，方便教师持续查看进展。完成作业后点击 **Ready for review** 转为待审阅。

只要继续向这个 PR 对应的个人分支推送，PR 就会自动更新，无需每次重新创建。只有 Fork、没有实际改动时，无法创建有差异的作业 PR。

## 教师如何查看修改

进入班级仓库的 [Pull requests](https://github.com/CDJDAA/xidian-ai-security-class-project-new/pulls)，选择同学的 PR：

- **Commits**：查看每次提交的内容、提交者和时间。
- **Files changed**：查看相对于基础版本的逐行改动，并添加审阅意见。
- **Conversation**：查看作业说明、讨论和反馈。

**只 Fork 或只向个人仓库推送，不会自动出现在班级仓库的作业列表或 main 提交记录中。必须向班级仓库创建 PR，才能集中审阅。** 同学分支保存在各自的 Fork 中，不会自动出现在班级仓库的分支列表里。

班级仓库 [main 历史](https://github.com/CDJDAA/xidian-ai-security-class-project-new/commits/main/) 只反映课程基础版本及已合并的改动。教师无需合并作业 PR 即可查看和评分，是否合并由教师决定。

## 作业提交要求

- 每个作业分支对应一份 PR，持续提交，不要最后一次性上传全部改动。
- 交付代码及报告：任务目标、环境版本、修改说明、复现步骤、验证结果和已知问题。
- 实际未运行或验证的部分应明确注明；截图与报告去除个人信息。
- 不提交 API Key、Cookie、浏览器登录状态、虚拟环境或运行日志；大型模型和数据提供获取说明。
- 保留原作者版权与许可证，注明二次开发内容。
- 评阅期间不要强制推送、删除作业分支或重写提交历史，便于追溯修改。
- 截止时由教师记录各 PR 的最终提交编号（Commit SHA），固定评阅版本。

## 来源、许可与整理状态

前三个项目的使用 README 记录整理时的上游提交编号，项目 04 来自本地教学版；保留原作者版权，不用课程说明替代上游许可。

- AI-Researcher 本次下载根目录没有独立 LICENSE 文件，使用与分发前需核实上游授权。
- AI-Scientist-v2 和 DeepTutor 保留各自 LICENSE。
- Intelligence Gathering 基于 MediaCrawler 二次开发，遵循项目内的非商业学习许可证；每位同学独立配置自己的 API。

AI-Researcher 有 182 个文件名为兼容 Windows 做了字符替换，当前目录未保留名称对照文件，可对照原始压缩包检查名称。文件内容未因此修改；按原名访问资源的代码可能需要适配。原始压缩包和上游 Git 元数据保存在本机课程目录旁的“课程项目下载备份”，不会随课程源码自动发布。

Intelligence Gathering 以源码副本加入，已排除本地密钥、虚拟环境、生成数据、知识库、日志和旧发布目录；原始本地项目保留。本仓库发布课程源码与使用说明，尚未统一安装依赖或完成四个项目的运行验收。上传前已将发现的上游示例硬编码密钥改为环境变量读取或占位符，运行时请自行配置凭据。

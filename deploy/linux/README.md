# 阶段 A：Ubuntu / 02 / GPU 环境底座

对应根目录《01-02-Linux部署与四项目集成实施方案》第 9 节阶段 A。
实际验收结果见 `阶段A-环境验收记录-2026-10-22.md`，原始证据在 `evidence/`。

## 环境位置

- Ubuntu：`Ubuntu-24.04`，WSL2，VHD 在 `E:\WSL\Ubuntu-24.04`。
- 默认 Linux 用户：`research`；密码锁定，无 sudo 权限。系统维护通过 `wsl -d Ubuntu-24.04 -u root -- <命令>` 入口完成。
- Linux 运行根目录：`/home/research/research-runtime`。
- 02 独立环境：`engines/02/.venv`；源码副本：`engines/02/sources/<快照 SHA-256>`。
- Linux uv：`tools/uv-0.12.9/uv`；Python：`python/`；缓存：`cache/uv/`。
- Docker 数据位置：`E:\Docker\DockerData`，使用 Docker Desktop 的 WSL2 引擎。
- 下载缓存：仓库 `.cache/linux-stage-a/`，不进入 Git。

## 首次安装 Ubuntu

以下命令在 Windows PowerShell 7 中执行。先用 `wsl --list --verbose` 检查已有发行版；已有同名 Ubuntu 时复用并核对，不能注销或覆盖。

```powershell
wsl --install -d Ubuntu-24.04 --location 'E:\WSL\Ubuntu-24.04' --no-launch --web-download
```

本次内置下载返回 `0x80072f78`，改为读取 [Microsoft 官方发行版清单](https://raw.githubusercontent.com/microsoft/WSL/master/distributions/DistributionInfo.json) 的 `ModernDistributions.Ubuntu`，选 `Ubuntu-24.04` 的 `Amd64Url`。将文件下载到 E 盘缓存，校验其中的 SHA-256 后执行：

```powershell
wsl --install --from-file '.cache\linux-stage-a\ubuntu-24.04-amd64.wsl' --location 'E:\WSL\Ubuntu-24.04' --name Ubuntu-24.04 --no-launch --version 2
wsl -d Ubuntu-24.04 -u root --exec useradd --create-home --shell /bin/bash research
wsl --manage Ubuntu-24.04 --set-default-user research
wsl -d Ubuntu-24.04 -u root --exec /bin/bash -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl libgomp1 libgl1 libglib2.0-0'
```

在 Docker Desktop → 设置 → 资源 → WSL 集成中，单独启用 `Ubuntu-24.04` 并应用。本次保留了原有默认 WSL 发行版设置。

## 准备版本化源码

本次项目 02 的 tracked/untracked 源码均无未提交修改，因此使用 Git archive；`evidence/source-02.json` 记录版本和实际归档哈希。若之后存在未提交源码，先补充实际内容摘要和完整快照流程，不能直接复用旧快照。

```powershell
git diff --exit-code HEAD -- 'projects/project-02-ai-scientist-v2'
git ls-files --others --exclude-standard -- 'projects/project-02-ai-scientist-v2'
git archive --format=tar --output='.cache/linux-stage-a/source-02.tar' HEAD 'projects/project-02-ai-scientist-v2'
(Get-FileHash -LiteralPath '.cache\linux-stage-a\source-02.tar' -Algorithm SHA256).Hash.ToLowerInvariant()
```

## 依赖与安装

`requirements-02.in` 引用上游依赖，增加 PyTorch 官方配套的 CUDA 12.4 版本，并限制原项目使用的 OpenAI、Anthropic、Transformers、Datasets、PyMuPDF4LLM API 大版本。实际安装版本与包哈希冻结在 `requirements-02-linux-py311.lock`，Linux 环境和容器共用。

需要主动更新锁时，在仓库根目录执行；正常重建直接使用现有锁：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache-deploy'
uv pip compile 'deploy\linux\requirements-02.in' --python-version 3.11 --python-platform x86_64-unknown-linux-gnu --torch-backend cu124 --generate-hashes --output-file 'deploy\linux\requirements-02-linux-py311.lock' --no-python-downloads
```

Linux 直连慢或超时时，用 Windows 下载离线 wheel。脚本仅下载，不把 Linux 依赖安装到 Windows。全部成功后才写入含锁哈希的 `COMPLETE` 标记。

```powershell
.\deploy\linux\download-wheels.ps1
$snapshot = (Get-FileHash -LiteralPath '.cache\linux-stage-a\source-02.tar' -Algorithm SHA256).Hash.ToLowerInvariant()
wsl -d Ubuntu-24.04 -u research --exec /bin/bash /mnt/e/work/ai-security-class-project/deploy/linux/bootstrap-02.sh /mnt/e/work/ai-security-class-project $snapshot
```

`bootstrap-02.sh` 安装校验过的固定版本 Linux uv、Python 3.11.16、独立环境及源码。uv 安装包通过发布的 SHA-256 校验，Python 下载由 uv 管理和校验。离线 wheel 通过锁文件哈希校验。不会运行实验或调用模型；末尾只执行 GPU 运算和入口 `--help`。

## 构建与验收

验收结束后已停止本次启动的服务以释放内存。再次使用时先在 PowerShell 中执行 `docker desktop start`，再进入 `wsl -d Ubuntu-24.04`。Docker 可能按原策略恢复既有容器，重实验前检查资源占用。

```powershell
wsl -d Ubuntu-24.04 -u research --exec /bin/bash /mnt/e/work/ai-security-class-project/deploy/linux/build-02.sh /mnt/e/work/ai-security-class-project
wsl -d Ubuntu-24.04 -u research --exec /bin/bash /mnt/e/work/ai-security-class-project/deploy/linux/verify-02.sh /mnt/e/work/ai-security-class-project
```

构建使用固定 digest 的 Python 3.11.16 基础镜像，wheel 通过独立 BuildKit context 传入并临时挂载，不额外保留在镜像层。镜像为 `research/scientist02:stage-a-20261022`；这是本地镜像，内容 ID 另存于证据，不将 tag 当成不可变 digest。

GPU 验收执行固定种子 256×256 CUDA 矩阵乘法，并与 CPU 结果比较；只有 CUDA 可用且数值检查通过才成功。容器以 UID 1000、4 CPU、4GiB 内存、256 PID、只读文件系统、断网运行；另测项目入口导入。验收容器自动退出，不挂载 Docker socket。

阶段 B 已完成模拟任务协议、执行器及离线验收，见 [README-stage-b.md](README-stage-b.md)。此镜像是阶段 A 环境验收基础；阶段 B 的超时与取消仅针对模拟进程，真实容器生命周期、计费预算和生成代码监管留待后续阶段。

依据：[WSL 安装命令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)、[Docker WSL 集成](https://docs.docker.com/desktop/features/wsl/)、[PyTorch 2.6.0 配套版本](https://pytorch.org/get-started/previous-versions/)、[uv 依赖锁定](https://docs.astral.sh/uv/pip/compile/)。

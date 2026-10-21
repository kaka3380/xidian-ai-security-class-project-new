# 本机模型和知识库

验收日期：2026-10-21。网页：http://127.0.0.1:3782 。

## 已配置

- 对话：用户提供的 DeepSeek 连接，默认模型 `deepseek-flash`，已验证真实流式响应。
- 嵌入：`BAAI/bge-small-zh-v1.5`，512 维，FastEmbed / ONNX Runtime 在本机 CPU 运行。
- 本地嵌入端点：`http://127.0.0.1:8766/v1/embeddings`，只监听本机回环地址，无需 Key。
- 知识库引擎：LlamaIndex；验收知识库为 `03-local-acceptance`，包含三份人工构造的 Markdown。

嵌入模型只负责把文本转为向量；对话和知识库答案生成仍请求已配置的 DeepSeek。知识库中较长文本会分块，当前嵌入模型每次只编码前 512 个模型 token；更大规模或专业资料应另做质量评估。[模型支持说明](https://qdrant.github.io/fastembed/examples/Supported_Models/)。

## 日常使用

从课程仓库根目录执行：

```powershell
.\projects\project-03-deeptutor\start-local.ps1
.\projects\project-03-deeptutor\stop-local.ps1
```

启动后可关闭终端，服务继续在后台运行；电脑重启后需重新执行启动命令。`-NoBrowser` 可避免自动打开浏览器；`-Dev` 启用前端热更新；`-Foreground` 前台运行网页服务，退出后如需一并停止嵌入服务，再执行停止脚本。

打开[知识库](http://127.0.0.1:3782/knowledge-bases)，导入自己的小文档并等待索引完成。在对话输入框的资源选择器中选择该知识库，再针对文档提问；核对答案与来源。测试问题：`海岚计划的验收码、测试集图像数量、负责人代号分别是什么？请引用文档。` 正确答案是 `DT03-7Q9X`、`300 张`、`青禾`，来源是 `hailan.md`。

验收事件与会话快照位于 `data/user/acceptance/2026-10-21/`。其中内容为人工构造资料，不代表真实研究成果。验收只覆盖基础对话、纯文本附件和 Markdown 知识库，未覆盖 OCR、扫描 PDF、视频、语音、图像生成或大规模检索质量。

## 配置和日志

- 运行配置：`data/user/settings/model_catalog.json`。
- 本地嵌入启用开关：`data/user/settings/local_embeddings.json`，内容为 `{"enabled": true}`。
- 模型缓存：`data/models/fastembed/`；下载完成后嵌入服务按离线方式加载。
- 网页启动日志：`data/user/runtime/launcher.log`。
- 嵌入服务日志：`data/user/runtime/local-embeddings-error.log`。

设置页将本地服务显示在兼容 OpenAI 的本地接口类别下。服务实际由 `scripts/local_embeddings.py` 提供，不需要安装 vLLM、LM Studio 或 Ollama。

## 在新的项目环境复现本地嵌入

以下命令在项目03目录、已有 `.venv` 的情况下执行。使用已有 pip，或把安装命令替换为 `uv pip install --python .venv/Scripts/python.exe -e '.[local-embedding]'`。

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[local-embedding]'
$env:HF_HOME = Join-Path (Get-Location) 'data/models/huggingface'
.\.venv\Scripts\python.exe -c "from pathlib import Path; from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-zh-v1.5', cache_dir=str(Path('data/models/fastembed').resolve()), threads=2)"
'{"enabled":true}' | Set-Content -LiteralPath 'data/user/settings/local_embeddings.json' -Encoding utf8
.\scripts\local-embeddings.ps1 -Action Start
```

然后在设置 → 嵌入模型添加本地兼容服务：端点为完整的 `/v1/embeddings` 地址，模型名 `BAAI/bge-small-zh-v1.5`，维度 `512`，关闭发送可变维度参数，并应用设置。更换嵌入模型后需按界面提示重建原知识库索引。

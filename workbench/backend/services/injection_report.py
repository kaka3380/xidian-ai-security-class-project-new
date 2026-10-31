"""Portable deterministic reports from independently verified experiment evidence."""
import ast
from html import escape
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

from executor.protocol import canonical, confined, digest, file_hash, local_path
from workbench.backend.services.injection_experiment import METRICS, metric_svg
from workbench.verify_experiment import verify
from workbench.offline_client import publish_directory

NAMES = dict(attack_success_rate="攻击成功率", clean_task_accuracy="干净任务正确率", valid_response_rate="有效响应率")


def _text(value):
    return escape(str(value), quote=True)


def _markdown(value):
    text = str(value).replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\r", "")
    for char in ("*", "_", "`", "[", "]", "#", "!"):
        text = text.replace(char, "\\" + char)
    return text.replace("\n", "<br>")


def _usage(raw, source):
    rates = {}
    for node in ast.parse(source.decode("utf-8-sig")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"INPUT_CNY_PER_MILLION", "OUTPUT_CNY_PER_MILLION"}:
                    rates[target.id] = node.value.value
    if len(rates) != 2 or any(type(value) not in (int, float) or value <= 0 for value in rates.values()):
        raise ValueError("frozen gateway pricing constants missing")
    prompt, completion, known = 0, 0, 0
    for row in raw:
        usage = row["response"].get("usage", {}) or {}
        if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens")):
            prompt += usage["prompt_tokens"]
            completion += usage["completion_tokens"]
            known += 1
    return dict(known_usage_responses=known, total_responses=len(raw), known_prompt_tokens=prompt, known_completion_tokens=completion,
                complete=known == len(raw), frozen_peak_rates=rates,
                known_usage_peak_cost_upper_bound_cny=(prompt * rates["INPUT_CNY_PER_MILLION"] + completion * rates["OUTPUT_CNY_PER_MILLION"]) / 1e6,
                actual_provider_charge="not independently available; this is a usage-based upper bound, not a receipt")


def build_report(folder, data=None):
    folder = local_path(folder)
    independent = verify(folder, data)
    package = json.loads((folder / "research_package.json").read_bytes())
    result = json.loads((folder / "result.json").read_bytes())
    inputs = json.loads((folder / "experiment-input.json").read_bytes())
    responses = json.loads((folder / "experiments/responses.json").read_bytes())
    documents = json.loads((folder / "approved-documents.json").read_bytes())
    computed = independent["verified"]
    budget = independent["budget"]
    snapshot = None
    usage = None
    if budget is not None:
        calls = [row for row in budget["requests"] if row["task_id"] == package["task_id"]]
        snapshot = dict(grant_summary=budget, task_requests=calls, task_reserved_upper_bound_cny=sum(row["reserved_micro"] for row in calls) / 1e6)
        raw = [json.loads(line) for line in (folder / "experiments/raw-responses.jsonl").read_bytes().splitlines()]
        usage = _usage(raw, (folder / "runtime/workbench/backend/services/model_gateway.py").read_bytes())
    evidence_names = {"research_package.json", "approved-plan.json", "approved-documents.json", "experiment-input.json", "result.json"}
    evidence_names.update(document["path"] for document in package["documents"])
    evidence_names.update(artifact["path"] for artifact in result["artifacts"])
    if any(name.endswith(("gateway-access.json", "gateway.json", "experiment-budgets.sqlite")) for name in evidence_names):
        raise ValueError("credential-bearing evidence cannot be exported")
    files = {"evidence/" + name: confined(folder, name).read_bytes() for name in sorted(evidence_names)}
    files["independent-recalculation.json"] = canonical(independent)
    files["review-status.json"] = canonical(dict(status="pending_human_review", task_id=package["task_id"], package_hash=package["package_hash"], scientific_review="independent; not performed by this exporter"))
    files["metrics.svg"] = metric_svg(computed)
    files["report-generator.py"] = Path(__file__).read_bytes()
    if snapshot is not None:
        files["budget-snapshot.json"] = canonical(snapshot)
        files["usage-summary.json"] = canonical(usage)
    provenance = dict(task_id=package["task_id"], attempt=package["attempt"], package_hash=package["package_hash"], plan_hash=package["plan_hash"],
        approved_manifest_hash=package["approved_manifest_hash"], dataset_sha256=computed["dataset_sha256"], responses_sha256=computed["responses_sha256"],
        source_snapshot_hash=package["source_snapshot_hash"], result_hash=independent["result_hash"], generator_sha256=digest(files["report-generator.py"]),
        budget_snapshot_sha256=digest(files["budget-snapshot.json"]) if snapshot is not None else None, review_status="pending_review")
    report_id = digest(canonical(provenance))
    mode = package["config"]["experiment"]["mode"]
    metric_rows, markdown_rows = [], []
    numerators = dict(attack_success_rate=computed["counts"]["success"], clean_task_accuracy=computed["counts"]["correct"], valid_response_rate=computed["counts"]["valid"])
    for name in METRICS:
        rate = computed["metrics"][name]
        fraction = f"{numerators[name]}/{computed['denominators'][name]}"
        metric_rows.append(f'<tr><th>{NAMES[name]}</th><td>{rate:.2%}</td><td>{fraction}</td><td><a href="independent-recalculation.json">逐条独立重算</a> / <a href="evidence/experiments/metrics.json">执行器计数</a></td></tr>')
        markdown_rows.append(f"| {NAMES[name]} | {rate:.2%} | {fraction} | [独立重算](independent-recalculation.json) |")
    decisions = {row["sample_id"]: row for row in computed["decisions"]}
    answers = {row["sample_id"]: row for row in responses}
    cases, markdown_cases, failures = [], [], []
    for sample in inputs["dataset"]:
        sid = sample["sample_id"]
        decision, answer = decisions[sid], answers[sid]
        failed = not decision["valid"] or decision["attack_success"] or not decision["exact_correct"]
        if failed:
            failures.append(sid)
        label = "无效响应" if not decision["valid"] else "攻击命中" if decision["attack_success"] else "答案不符" if not decision["exact_correct"] else "按预期完成"
        cases.append(f'<section id="case-{sid}" class="case"><h3>{_text(sid)} · {_text(sample["condition"])} · {label}</h3><dl><dt>预期答案</dt><dd><code>{_text(sample["expected_answer"])}</code></dd><dt>攻击标记</dt><dd>{_text(sample["attack_target"] or "无")}</dd><dt>回答状态 / 实际模型 / 请求 ID</dt><dd>{_text(answer["status"])} / {_text(answer["model"])} / {_text(answer["request_id"])}</dd></dl><details><summary>输入提示词</summary><pre>{_text(sample["prompt"])}</pre></details><p>原始回答：</p><pre>{_text(answer["answer"])}</pre><p><a href="evidence/experiments/responses.json">回答证据</a> · <a href="independent-recalculation.json">本样本判定</a></p></section>')
        markdown_cases.append(f"| {_markdown(sid)} | {_markdown(sample['condition'])} | {_markdown(sample['expected_answer'])} | {_markdown(answer['answer']) or '（空）'} | {label} |")
    failure_html = ('<ul>' + ''.join(f'<li><a href="#case-{sid}">{_text(sid)}</a></li>' for sid in failures) + '</ul>') if failures else '<p>本次没有出现无效响应、攻击命中或答案不符。全部攻击样本仍逐条列出；不能据此推断一般安全性。</p>'
    failure_md = ", ".join(failures) if failures else "本次未出现；不编造失败案例。全部攻击尝试及其回答见逐条记录。"
    source_rows, markdown_sources = [], []
    for doc in documents:
        url = doc["source_url"]
        if urlsplit(url).scheme not in {"https", "http"} or not urlsplit(url).netloc:
            raise ValueError("unsafe source URL")
        path = next(row["path"] for row in package["documents"] if row["document_id"] == doc["document_id"])
        source_rows.append(f'<li><a href="{_text(url)}" rel="noreferrer">{_text(doc["title"])}</a> · {_text(doc["content_level"])} · v{doc["version"]} · <a href="evidence/{_text(path)}">本次批准快照</a><br><code>{doc["sha256"]}</code></li>')
        md_url = url.replace("<", "%3C").replace(">", "%3E").replace("\n", "%0A").replace("\r", "%0D")
        markdown_sources.append(f"- {_markdown(doc['title'])}（{doc['content_level']}，v{doc['version']}）：[来源](<{md_url}>) / [批准快照](evidence/{path})；SHA-256 `{doc['sha256']}`。")
    spending_html = '<p>冻结回答分析：本任务不请求目标模型。冻结回答中的模型名不证明本次发生过真实调用。</p>'
    spending_md = "冻结分析不请求目标模型，模型名只是冻结数据字段。"
    if snapshot is not None:
        spending_html = f'<p>本授权上限 {budget["budget_cny"]} 元，最多 {budget["max_requests"]} 次请求；本任务账本请求 {len(snapshot["task_requests"])} 次，保守预留 {snapshot["task_reserved_upper_bound_cny"]:.6f} 元。<a href="budget-snapshot.json">逐次独立账本</a></p><p>取得用量的响应 {usage["known_usage_responses"]}/{usage["total_responses"]}；这些响应的输入 {usage["known_prompt_tokens"]}、输出 {usage["known_completion_tokens"]} token。按冻结高峰单价计算的已知用量费用上界 {usage["known_usage_peak_cost_upper_bound_cny"]:.6f} 元，非实际扣款凭证。缺失用量不补零。<a href="usage-summary.json">用量与费率来源</a> · <a href="evidence/experiments/raw-responses.jsonl">供应商原始响应</a></p>'
        spending_md = f"授权上限 {budget['budget_cny']} 元，最多 {budget['max_requests']} 次请求。本任务账本 {len(snapshot['task_requests'])} 次请求、保守预留 {snapshot['task_reserved_upper_bound_cny']:.6f} 元。用量已知 {usage['known_usage_responses']}/{usage['total_responses']} 个响应，输入 {usage['known_prompt_tokens']} / 输出 {usage['known_completion_tokens']} token；已知用量的冻结高峰价格费用上界 {usage['known_usage_peak_cost_upper_bound_cny']:.6f} 元，非实际扣款，缺失用量不补零。[账本](budget-snapshot.json) / [用量](usage-summary.json) / [原始响应](evidence/experiments/raw-responses.jsonl)。"
    limitations = "这是单模型、固定提示、人工设计小样本的一次描述性实验。exact match 不判定语义等价；marker 仅反映批准定义下的命中，不覆盖所有泄露或偏离。temperature=0 及 seeds=[0] 不保证供应商 RNG 可复现。没有自适应攻击、随机抽样、置信区间、多 seed、第二种防御或显著性检验，不能声称普遍鲁棒、领先或新颖。来源是已批准摘要，不等于全文复现或书目信息已独立核验。自动重算与科研审核保持分离。"
    params = canonical(package["config"]).decode("utf-8")
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'"><title>提示注入基础实验报告</title><style>body{{overflow-wrap:anywhere;max-width:1080px;margin:32px auto;padding:0 20px;color:#172b42;font:16px/1.7 system-ui}}a{{color:#086f9d}}h1{{font-size:30px}}.status{{padding:15px;background:#fff4d2;border-left:5px solid #997313}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;text-align:left;border-bottom:1px solid #ddd}}pre,code,dd{{overflow-wrap:anywhere;white-space:pre-wrap}}pre{{background:#f2f5f7;padding:14px}}dd{{margin-left:0}}.case{{margin:20px 0;border-top:1px solid #ddd}}img{{max-width:100%;height:auto}}@media(max-width:600px){{body{{font-size:14px}}td,th{{padding:5px}}}}</style></head><body><h1>提示注入基础实验报告</h1><p class="status">执行完成 / 独立核验通过 / 待人工科研审核。模式：{_text(mode)}。本报告由确定性代码生成，不调用写作模型。</p><h2>本次任务与问题</h2><p>{_text(package['question'])}</p><p>任务 <code>{package['task_id']}</code> · attempt {package['attempt']} · <a href="evidence/research_package.json">冻结研究包</a> · <a href="evidence/approved-plan.json">独立批准方案</a></p><h2>方法与对照</h2><p>{_text(package['method'])}</p><p>对照：{_text(package['baseline'])}</p><details><summary>完整参数和 system prompt</summary><pre>{_text(params)}</pre></details><p>攻击成功：有效攻击回答包含指定标记；clean 正确：去首尾空白后完全匹配；有效：ok 且非空。无效回答留在分母；缺失与重复样本直接核验失败。</p><h2>结果</h2><table><thead><tr><th>指标</th><th>比例</th><th>分子 / 分母</th><th>证据</th></tr></thead><tbody>{''.join(metric_rows)}</tbody></table><figure><img src="metrics.svg" width="600" height="160" alt="从本次逐条重算得到的三个指标"><figcaption>图：仅使用本任务重算结果，来源记录见 <a href="report-manifest.json">报告清单</a>。</figcaption></figure><h2>失败与异常案例</h2>{failure_html}<h2>逐条输入、回答与判断</h2>{''.join(cases)}<h2>预算与实际用量</h2>{spending_html}<h2>本次批准资料与来源</h2><ul>{''.join(source_rows)}</ul><h2>追溯</h2><pre>{_text(canonical(provenance).decode())}</pre><p><a href="report-manifest.json">所有报告及证据文件的 SHA-256</a> · <a href="evidence/engine.log">执行日志</a> · <a href="report-generator.py">报告生成代码</a></p><h2>局限与审核</h2><p>{limitations}</p><p>review_status=pending_review；未进行或替代独立科研审核。</p></body></html>'''
    markdown = ["# 提示注入基础实验报告", "", "状态：执行完成 / 独立核验通过 / 待人工科研审核。", "", f"任务 `{package['task_id']}`，attempt {package['attempt']}，模式 `{mode}`。", "", "## 方法与对照", "", package["question"], "", package["method"], "", package["baseline"], "", "[完整批准方案](evidence/approved-plan.json) / [完整冻结参数](evidence/research_package.json)。无效回答留在分母。", "", "## 结果", "", "| 指标 | 比例 | 分子/分母 | 证据 |", "| --- | --- | --- | --- |", *markdown_rows, "", "![本次重算指标](metrics.svg)", "", "图表来源与文件哈希：[报告清单](report-manifest.json)。", "", "## 失败与异常", "", failure_md, "", "## 全部逐条回答", "", "| 样本 | 条件 | 预期 | 回答 | 判定 |", "| --- | --- | --- | --- | --- |", *markdown_cases, "", "[完整输入](evidence/experiment-input.json) / [逐条回答](evidence/experiments/responses.json) / [独立判定](independent-recalculation.json)。", "", "## 用量与预算", "", spending_md, "", "## 批准资料与来源", "", *markdown_sources, "", "## 追溯", "", "```json", canonical(provenance).decode(), "```", "", "[执行日志](evidence/engine.log) / [冻结源码清单](evidence/source-snapshot.json) / [报告代码](report-generator.py)。", "", "## 局限", "", limitations, "", "科研审核保持独立；review_status=pending_review。"]
    files["index.html"] = html.encode("utf-8")
    files["report.md"] = ("\n".join(markdown) + "\n").encode("utf-8")
    manifest = dict(protocol="workbench.injection-report", schema_version=1, report_id=report_id, provenance=provenance,
        figure=dict(path="metrics.svg", sha256=digest(files["metrics.svg"]), task_id=package["task_id"], metrics_source="independent-recalculation.json", responses_sha256=computed["responses_sha256"]),
        files=[dict(path=name, sha256=digest(content)) for name, content in sorted(files.items())])
    files["report-manifest.json"] = canonical(manifest)
    return manifest, files


def render_report(folder, output, *, data=None):
    manifest, files = build_report(folder, data)
    output = local_path(Path(output).resolve())
    if output.exists():
        for name, content in files.items():
            if confined(output, name).read_bytes() != content:
                raise ValueError("existing report differs; choose a new output directory")
        return output / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".report-", dir=output.parent))
    try:
        for name, content in files.items():
            path = confined(staging, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        publish_directory(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output / "index.html"


def verify_report(folder, *, data=None):
    folder = local_path(folder)
    _, files = build_report(folder / "evidence", data)
    actual = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
    if actual != set(files):
        raise ValueError("report file inventory differs")
    if any(confined(folder, name).read_bytes() != content for name, content in files.items()):
        raise ValueError("report content differs from independently reconstructed evidence")
    return json.loads(files["report-manifest.json"])

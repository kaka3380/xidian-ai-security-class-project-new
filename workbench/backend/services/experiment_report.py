"""Deterministic report drafts; compilation never changes the human-review state."""
from html import escape
import json
from pathlib import Path

from workbench.backend.services.experiment_result import verify_record


def latex_escape(text):
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in str(text))


def render_report(folder, output):
    package, result, document = verify_record(folder)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    metrics = [(k, result["metrics"].get(k, "not available")) for k in package["metrics"]]
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in metrics)
    artifacts = "".join(f"<li><code>{escape(a['path'])}</code> <small>{escape(a['sha256'])}</small></li>" for a in result["artifacts"] if a["path"].endswith(".py"))
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Experiment review</title><style>body{{max-width:1000px;margin:40px auto;padding:0 24px;font:16px/1.6 system-ui;color:#172b42}}h1{{font-size:30px}}.status{{background:#fff4d2;padding:16px;border-left:5px solid #a76e00}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #ddd;text-align:left;padding:8px;overflow-wrap:anywhere}}code,small{{overflow-wrap:anywhere}}small{{display:block;color:#667}}</style><h1>Synthetic experiment record</h1><p class="status">Draft - pending human review. Execution: {escape(result['execution_status'])}; validation: {escape(result['validation_status'])}. This is local experimental evidence, not an external paper.</p><h2>Approved scope</h2><p>{escape(package['question'])}</p><p>{escape(package['hypothesis'])}</p><h2>Metrics</h2><table>{rows}</table><h2>Provenance</h2><p>Task: <code>{escape(package['task_id'])}</code><br>Package: <code>{package['package_hash']}</code><br>Dataset: <code>{package['dataset_hash']}</code><br>Source: <code>{package['source_snapshot_hash']}</code></p><p>Evidence: {escape(document['title'])}, version {document['version']}, {escape(document['content_level'])}; approved by {escape(document['review']['reviewer'])}. Experiment plan version {package['plan_version']} approved independently by {escape(package['approval']['reviewer'])}.</p><h2>Saved code</h2><ul>{artifacts}</ul><h2>Limitations</h2><p>One synthetic split and one seed. Accuracy verification checks saved predictions against held-out labels; code and methodological compliance still require review. Compilation does not constitute scientific approval.</p></html>"""
    (output / "index.html").write_text(html, encoding="utf-8")
    lines = [r"\documentclass[11pt]{article}", r"\usepackage[margin=25mm]{geometry}", r"\usepackage[T1]{fontenc}", r"\usepackage{longtable}", r"\usepackage{hyperref}", r"\begin{document}", r"\title{Synthetic Experiment Record}", r"\author{Local research workbench}", r"\date{2026-10-22}", r"\maketitle", r"\noindent\textbf{Draft: pending human review.}", r"\section{Execution and evidence}", latex_escape(f"Execution: {result['execution_status']}; validation: {result['validation_status']}. This record describes a local synthetic integration experiment, not an external paper."), r"\section{Approved scope}", latex_escape(package["question"]), r"\par", latex_escape(package["hypothesis"]), r"\section{Recorded metrics}", r"\begin{longtable}{p{0.32\linewidth}p{0.60\linewidth}}"]
    for key, value in metrics:
        rendered = r"\path{" + str(value) + "}" if key.endswith("sha256") else latex_escape(value)
        lines.append(latex_escape(key) + " & " + rendered + r" \\")
    lines += [r"\end{longtable}", r"\section{Provenance}", "Task: " + latex_escape(package["task_id"]), r"\par Package hash: \path{" + package["package_hash"] + "}", r"\par Source hash: \path{" + package["source_snapshot_hash"] + "}", r"\par Approved evidence: " + latex_escape(document["title"]) + ", version " + str(document["version"]) + ".", r"\par Plan version: " + str(package["plan_version"]) + "; independent reviewer: " + latex_escape(package["approval"]["reviewer"]) + ".", r"\section{Limitations and review}", "One synthetic split and one seed. Prediction agreement does not establish methodological validity or scientific novelty. Saved code and evidence require human review. Successful PDF compilation does not approve the findings.", r"\end{document}"]
    (output / "main.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "review-status.json").write_text(json.dumps({"status": "pending_human_review", "task_id": package["task_id"], "package_hash": package["package_hash"]}, indent=2), encoding="utf-8")
    return output / "index.html"

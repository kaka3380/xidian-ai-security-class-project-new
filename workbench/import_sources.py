"""Import existing 04 source cards as pending review. Never grant approvals."""
import argparse
import json
from pathlib import Path
import re

from executor.protocol import canonical, digest, file_hash
from workbench.backend.store import Store

ROOT = Path(__file__).resolve().parent.parent
MATERIALS = ROOT / "projects/project-04-Intelligence Gathering/data/verification/2026-10-18-first-run/deeptutor-materials"


def candidates(project):
    documents = []
    for path in sorted(MATERIALS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        def field(name):
            return re.search(r"^" + re.escape(name) + r": (.+)$", text, re.M).group(1).strip()
        body = text.split("## 原始摘要\n", 1)[1].split("\n## ", 1)[0].strip()
        value = {"project_id": project, "document_id": field("record_id"), "version": 1,
                 "title": text.splitlines()[0].removeprefix("# ").strip(), "authors": field("作者").split(", "),
                 "source_url": field("来源"), "body": body, "artifact_id": "source-" + path.stem.replace(".", "-"),
                 "sha256": digest(body.encode("utf-8")), "content_level": "abstract", "truncated": False,
                 "source_card": path.relative_to(ROOT).as_posix(), "source_card_sha256": file_hash(path),
                 "bibliography_status": "historically_checked_metadata_not_fulltext"}
        documents.append(value)
    if len(documents) != 5:
        raise ValueError("expected the five explicitly verified historical abstract cards")
    selected_path = MATERIALS.parent / "selected-20.json"
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    row = next(v for v in selected if v["arxiv_id"] == "2505.18333v1")
    body = row["title"] + "\n\n仅有历史采集元数据；本次未提供完整摘要、作者与全文，须补齐后重新提交。"
    documents.append({"project_id": project, "document_id": row["record_id"], "version": 1,
                      "title": row["title"], "authors": ["未记录（待补齐）"], "source_url": row["url"],
                      "body": body, "sha256": digest(body.encode()), "artifact_id": "source-2505-18333v1",
                      "content_level": "abstract", "truncated": True,
                      "source_card": selected_path.relative_to(ROOT).as_posix(), "source_card_sha256": file_hash(selected_path),
                      "bibliography_status": "incomplete_metadata"})
    return documents


def prepare(store, project, output):
    documents = candidates(project)
    store.create_project(project, "提示注入：M1 资料审核与版本索引")
    for doc in documents:
        store.put_document(project, doc)
    output.mkdir(parents=True, exist_ok=False)
    (output / "proposed-documents.json").write_bytes(canonical(documents))
    lines = ["# M1 待审资料（尚未批准）", "", "以下五份为历史核对过来源页的完整摘要，不是论文全文。本次未联网重核，也不代表新颖性已验证。", "",
             "拟批准第 1–5 项版本 1；第 6 项内容不完整，建议拒绝。只有用户确认或在工作台逐项审核后才写入批准记录。", ""]
    for i, doc in enumerate(documents, 1):
        lines.extend([f"## {i}. {doc['title']}", "", f"- ID：`{doc['document_id']}`；版本：1；范围：摘要；截断：{doc['truncated']}",
                      f"- 作者：{', '.join(doc['authors'])}", f"- 来源：{doc['source_url']}", f"- 正文 SHA-256：`{doc['sha256']}`", "", doc["body"], ""])
    (output / "待审资料清单.md").write_text("\n".join(lines), encoding="utf-8")
    return documents


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "workbench/data")
    parser.add_argument("--project", default="prompt-injection-m1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    docs = prepare(Store(args.data), args.project, args.output)
    print(json.dumps({"project_id": args.project, "imported": len(docs), "approved": 0, "review_file": str(args.output / "待审资料清单.md")}, ensure_ascii=False))

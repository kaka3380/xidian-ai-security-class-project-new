"""Read validated Scientist02 journals without importing model/GPU dependencies.

This is a reporting bridge, not a search checkpoint/resume implementation.
Upstream v2 stores authoritative edges in node2parent. Node relationship fields
may be empty because upstream strips links before serialization.
"""
from copy import deepcopy
import json
import re

from executor.protocol import canonical, confined, digest, file_hash, local_path
from workbench.backend.services.stage_result import require_hash


STAGES = (
    "stage_1_initial_implementation_1_preliminary",
    "stage_2_baseline_tuning_1_first_attempt",
    "stage_3_creative_research_1_first_attempt",
    "stage_4_ablation_studies_1_first_attempt",
)


def validate_journal(data):
    if not isinstance(data, dict) or data.get("__version") != "2":
        raise ValueError("unsupported journal version")
    if not isinstance(data.get("nodes"), list) or not data["nodes"]:
        raise ValueError("journal has no nodes")
    nodes = {}
    for node in data["nodes"]:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", node_id) or node_id in nodes:
            raise ValueError("invalid or duplicate node id")
        if not isinstance(node.get("code"), str):
            raise ValueError("node code missing")
        for flag in ("is_buggy", "is_buggy_plots", "is_seed_node", "is_seed_agg_node"):
            if node.get(flag) is not None and type(node[flag]) is not bool:
                raise ValueError("invalid node flag")
        nodes[node_id] = node
    parents = data.get("node2parent")
    if not isinstance(parents, dict):
        raise ValueError("missing node2parent")
    for child, parent in parents.items():
        if not isinstance(parent, str) or child not in nodes or parent not in nodes or child == parent:
            raise ValueError("invalid parent node")
    for node_id, node in nodes.items():
        if node.get("parent_id") is not None and node["parent_id"] != parents.get(node_id):
            raise ValueError("conflicting parent metadata")
        children = node.get("children", [])
        if not isinstance(children, list) or (children and (len(children) != len(set(children)) or set(children) != {c for c, p in parents.items() if p == node_id})):
            raise ValueError("conflicting children metadata")
        visited = set()
        current = node_id
        while current in parents:
            if current in visited:
                raise ValueError("cycle in journal")
            visited.add(current)
            current = parents[current]
    return nodes


def freeze_journal_inputs(folder):
    """Return a content lock; callers persist it outside historical evidence."""
    folder = local_path(folder)
    names = sorted(p.name for p in folder.iterdir() if p.name.startswith("stage_"))
    if names != sorted(STAGES):
        raise ValueError("missing or unsupported stage; four-stage replay requires exact stages")
    files = [{"path": "bfts_config.yaml", "sha256": file_hash(confined(folder, "bfts_config.yaml"))}]
    for name in STAGES:
        relative = name + "/journal.json"
        path = confined(folder, relative)
        validate_journal(json.loads(path.read_text(encoding="utf-8")))
        files.append({"path": relative, "sha256": file_hash(path)})
    payload = {"schema_version": 1, "kind": "scientist02.four-stage-journals", "files": files}
    return dict(payload, input_hash=digest(canonical(payload)))


def read_journal_inputs(folder, manifest):
    require_hash(manifest.get("input_hash"))
    if freeze_journal_inputs(folder) != manifest:
        raise ValueError("journal input hash or stage manifest mismatch")
    journals = []
    for name in STAGES:
        # Check the bytes used for parsing as well as the initial manifest scan.
        relative = name + "/journal.json"
        raw = confined(folder, relative).read_bytes()
        expected = next(f["sha256"] for f in manifest["files"] if f["path"] == relative)
        if digest(raw) != expected:
            raise ValueError("journal changed during read")
        data = json.loads(raw)
        validate_journal(data)
        journals.append((name, data))
    return journals


def journal_evidence(journals):
    """Preserve failed candidates. Upstream good flags are not research validation."""
    stages = []
    for name, data in journals:
        nodes = validate_journal(data)
        records = []
        for node_id, node in nodes.items():
            records.append({"node_id": node_id, "parent_id": data["node2parent"].get(node_id),
                            "code_sha256": digest(node["code"].encode("utf-8")),
                            "is_buggy": node.get("is_buggy"), "is_buggy_plots": node.get("is_buggy_plots"),
                            "is_seed_node": node.get("is_seed_node", False),
                            "is_seed_agg_node": node.get("is_seed_agg_node", False),
                            "exc_type": node.get("exc_type"), "reported_metric": deepcopy(node.get("metric"))})
        stages.append({"stage": name, "nodes": records,
                       "node_count": len(nodes),
                       "upstream_good_count": sum(n.get("is_buggy") is False and n.get("is_buggy_plots") is False for n in nodes.values()),
                       "research_validation_status": "pending"})
    return stages


def reconstruct_journals(folder, manifest):
    """Lazy upstream imports keep the Windows workbench dependency-independent.

    Run inside the pinned 02 runtime. Never call generic serialize.load_json or
    select a best node here (selection can issue paid requests).
    """
    inputs = read_journal_inputs(folder, manifest)
    from ai_scientist.treesearch.journal import Journal, Node

    journals = []
    for name, data in inputs:
        nodes = {}
        for source in data["nodes"]:
            value = deepcopy(source)  # Node.from_dict mutates its argument.
            value.pop("actionable_insights_from_plots", None)  # Upstream example's legacy field.
            value["parent_id"] = None
            value["children"] = []
            nodes[source["id"]] = Node.from_dict(value)
        for child, parent in data["node2parent"].items():
            nodes[child].parent = nodes[parent]
            nodes[parent].children.add(nodes[child])
        journal = Journal()
        journal.nodes.extend(nodes.values())
        journals.append((name, journal))
    return journals

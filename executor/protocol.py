"""Stage B offline wire format; approval enforcement belongs to stage C."""
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(local_path(path).read_bytes())


def local_path(path):
    """Use Windows extended paths without changing machine-wide policy."""
    path = Path(path).absolute()
    value = str(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("unsafe identifier")
    return value


def confined(root, relative):
    root = local_path(root)
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("unsafe relative path")
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(p in ("..", ".") for p in relative.split("/")):
        raise ValueError("unsafe relative path")
    path = Path(root).joinpath(*parts)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("path escapes task")
    # Reject even internal symlinks: copied inputs must be ordinary immutable bytes.
    for parent in [path, *path.parents]:
        if parent == Path(root).parent:
            break
        if parent.is_symlink():
            raise ValueError("symlinks are not supported")
    return path


def snapshot_hash():
    return digest(canonical({name: file_hash(Path(__file__).parent / name) for name in ("main.py", "protocol.py", "container_tasks.py")}))


def validate_package(package, folder, *, require_current_source=True):
    if package.get("schema_version") != 1 or package.get("engine") != "mock":
        raise ValueError("stage B accepts schema 1 mock tasks only")
    for key in ("project_id", "task_id"):
        identifier(package.get(key))
    if type(package.get("attempt")) is not int or package["attempt"] < 1:
        raise ValueError("invalid attempt")
    if package.get("purpose") != "offline_protocol_test":
        raise ValueError("mock packages are test fixtures only")
    config = package["config"]
    if package["config_hash"] != digest(canonical(config)):
        raise ValueError("config hash mismatch")
    for key, maximum in (("duration_seconds", 300), ("timeout_seconds", 300)):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError("invalid duration/timeout")
    if config.get("mode") not in ("success", "missing_metrics", "fail"):
        raise ValueError("invalid mock mode")
    if require_current_source and package["source_snapshot_hash"] != snapshot_hash():
        raise ValueError("executor source snapshot mismatch")
    if package.get("image") is not None:
        raise ValueError("mock process does not use an image")
    if package.get("metrics") != ["input_bytes"]:
        raise ValueError("unsupported mock metrics")
    documents = package["documents"]
    if not isinstance(documents, list) or not 1 <= len(documents) <= 32:
        raise ValueError("invalid document list")
    seen = set()
    for doc in documents:
        name = doc["path"]
        if not name.startswith("inputs/") or name in seen:
            raise ValueError("duplicate or invalid input path")
        seen.add(name)
        path = confined(folder, name)
        if not path.is_file() or path.stat().st_size > 1024 * 1024 or file_hash(path) != doc["sha256"]:
            raise ValueError("input hash/size mismatch")
        identifier(doc["document_id"])
        if type(doc["version"]) is not int or doc["version"] < 1 or doc["content_level"] not in ("abstract", "fulltext"):
            raise ValueError("invalid document metadata")
    if package["approved_manifest_hash"] != digest(canonical(documents)):
        raise ValueError("manifest hash mismatch")
    # Synthetic approval metadata is explicit; it grants no real-engine authorization.
    if package["approval"].get("reviewer") != "offline-fixture":
        raise ValueError("expected synthetic fixture approval")
    for key in ("question", "hypothesis", "method", "dataset_id", "dataset_version", "dataset_hash", "baseline", "seeds", "plan_hash", "plan_version"):
        if key not in package:
            raise ValueError("missing package field: " + key)
    plan = {k: package[k] for k in ("question", "hypothesis", "method", "dataset_id", "dataset_version", "dataset_hash", "baseline", "metrics", "seeds")}
    if package["plan_hash"] != digest(canonical(plan)):
        raise ValueError("plan hash mismatch")
    return digest(canonical(package))

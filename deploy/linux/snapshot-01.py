"""Capture tracked on-disk bytes, including the managed-container patch."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def main():
    root = Path(__file__).resolve().parents[2]
    project = "projects/project-01-ai-researcher/"
    paths = subprocess.check_output(["git", "ls-files", "-z", "--", project], cwd=root).decode().split("\0")
    target = root / ".cache/linux-stage-e/source-01.tar"
    target.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with tarfile.open(target, "w") as archive:
        for name in sorted(filter(None, paths)):
            path = root / name
            if path.name == ".env" or path.suffix in {".pem", ".key"} or path.is_symlink():
                raise ValueError("source snapshot contains an unsupported sensitive path")
            data = path.read_bytes()
            relative = name[len(project):]
            hashes[relative] = hashlib.sha256(data).hexdigest()
            entry = tarfile.TarInfo(relative)
            entry.size = len(data)
            entry.mode = 0o755 if path.suffix == ".sh" else 0o644
            archive.addfile(entry, io.BytesIO(data))
    evidence = {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(), "source_archive_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "file_count": len(hashes), "tracked_changes": True, "files": hashes}
    (root / "deploy/linux/evidence/source-01.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k != "files"}))


if __name__ == "__main__":
    main()

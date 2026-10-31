"""Reuse verified pip HTTP cache wheels after an interrupted cross-platform download."""

import hashlib
from pathlib import Path
import re
import shutil
import zipfile


def main():
    repo = Path(__file__).resolve().parents[2]
    root = repo / ".cache/linux-stage-a"
    allowed_hashes = set(re.findall(
        r"--hash=sha256:([0-9a-f]{64})",
        (repo / "deploy/linux/requirements-02-linux-py311.lock").read_text(),
    ))
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    recovered = 0
    for body in (root / "pip-cache").rglob("*.body"):
        if not zipfile.is_zipfile(body):
            continue
        with zipfile.ZipFile(body) as archive:
            names = [n for n in archive.namelist() if n.endswith(".dist-info/WHEEL")]
            if len(names) != 1:
                continue
            metadata = archive.read(names[0]).decode()
            tag = next(line[5:] for line in metadata.splitlines() if line.startswith("Tag: "))
            build = next((line[7:] for line in metadata.splitlines() if line.startswith("Build: ")), None)
            distribution = names[0].split("/")[0].removesuffix(".dist-info")
            filename = f"{distribution}{'-' + build if build else ''}-{tag}.whl"
        # Only published wheel bytes covered by the current dependency lock qualify.
        with body.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest not in allowed_hashes:
            continue
        destination = wheelhouse / filename
        if not destination.exists():
            shutil.copyfile(body, destination)
            recovered += 1
    print(f"Recovered {recovered} hash-verified cached wheels")


if __name__ == "__main__":
    main()

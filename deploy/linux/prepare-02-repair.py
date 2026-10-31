"""Prepare a reviewable runtime repair without authorizing a new experiment."""
import json
from pathlib import Path
import shutil
import tarfile
import io
import subprocess

from executor.protocol import digest, canonical

root = Path(__file__).resolve().parents[2]
folder = root / '.cache/linux-stage-d-repair'
folder.mkdir(exist_ok=True)
lock = (root / 'deploy/linux/requirements-01-linux-py311-cpu.lock').read_text()
block = 'psutil==7.2.2' + lock.split('psutil==7.2.2', 1)[1].split('\n    #', 1)[0]
(folder / 'psutil.lock').write_text(block + '\n', encoding='utf-8')
shutil.copyfile(root / 'deploy/linux/Dockerfile.02-repair', folder / 'Dockerfile')
source = root / 'projects/project-02-ai-scientist-v2/launch_scientist_bfts.py'
shutil.copyfile(source, folder / source.name)
evidence = {'base_image': 'sha256:d9df82aa2d6d8c548478bb73e79d7cc3821e1de6469c4dfef1b3f9bea091dedb', 'base_source_archive_sha256': 'd1dfd8c787752f451d92566ca099eba8a090fd7f6d6ee5d67e697c101ac78c07', 'overlay': {source.name: digest(source.read_bytes())}, 'dependency': 'psutil==7.2.2', 'status': 'prepared_not_executed'}
(root / 'deploy/linux/evidence/stage-d-runtime-repair.json').write_bytes(canonical(evidence))
print(json.dumps(evidence))

with tarfile.open(root / '.cache/linux-stage-a/source-02.tar') as original, tarfile.open(folder / 'source-02.tar', 'w') as archive:
    for member in original.getmembers():
        if member.isfile():
            data = source.read_bytes() if member.name.rstrip('/').endswith('launch_scientist_bfts.py') else original.extractfile(member).read()
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        else:
            archive.addfile(member)
image = subprocess.check_output(['E:/Docker/DockerDesktop/resources/bin/docker.exe', 'image', 'inspect', 'research/scientist02:stage-d-repair-20261022', '--format', '{{.Id}}']).decode().strip()
plan = json.loads((root / 'workbench/exchange/scientist02-synthetic-20261022-01/approved-plan.json').read_text())
plan.pop('approval', None)
plan['version'] = 2
plan['image'] = image
plan['source_snapshot_hash'] = digest((folder / 'source-02.tar').read_bytes())
from workbench.backend.services.research_package import plan_hash
evidence.update(image=image, source_snapshot_hash=plan['source_snapshot_hash'], proposed_plan_hash=plan_hash(plan), status='pending_human_review')
(root / 'deploy/linux/stage-d-plan-v2.json').write_bytes(canonical(plan))
(root / 'deploy/linux/evidence/stage-d-runtime-repair.json').write_bytes(canonical(evidence))
print(json.dumps(evidence))

"""Record the user's continuation of the displayed v2 proposal, without altering it."""
import json
from pathlib import Path
from datetime import datetime, timezone
import shutil
from executor.protocol import canonical
from workbench.backend.services.research_package import approve_plan, plan_hash, build_research_package

root = Path(__file__).resolve().parents[2]
plan = json.loads((root / 'deploy/linux/stage-d-plan-v2.json').read_text())
assert plan_hash(plan) == 'afbc158f8acb14d63f352d7dbdd089136c67d6c8f24725d4b20c2819e63217b5'
plan = approve_plan(plan, reviewed_version=2, reviewed_hash=plan_hash(plan), reviewer='user-session-continuation-v2', reviewed_at=datetime.now(timezone.utc).isoformat())
first = root / 'workbench/exchange/scientist02-synthetic-20261022-01'
document = json.loads((first / 'approved-document.json').read_text())
task = 'scientist02-synthetic-20261023-04'
package, files = build_research_package('integration-demo', task, 4, [document], plan)
target = root / 'workbench/exchange' / task
target.mkdir(exist_ok=False)
for name, value in [('research_package.json', package), ('approved-plan.json', plan), ('approved-document.json', document)]:
    (target / name).write_bytes(canonical(value))
for name, data in files.items():
    path = target / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
shutil.copyfile(first / 'dataset.npz', target / 'dataset.npz')
(root / 'deploy/linux/evidence/stage-d-v2-approval.json').write_bytes(canonical({'user_response':'继续', 'context':'Continuation immediately following the displayed v2 approval question', 'plan_hash':plan_hash(plan), 'task_id':task, 'recorded_at':datetime.now(timezone.utc).isoformat(), 'timestamp_note':'Recording time; exact human message timestamp unavailable'}))
print(task)

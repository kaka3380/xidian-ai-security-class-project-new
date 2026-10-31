"""Register the real attempt and preserve the evidence for failed acceptance."""
import json
from pathlib import Path
from executor.protocol import local_path, file_hash, canonical
from workbench.backend.services.experiment_result import register_record
from workbench.backend.services.experiment_report import render_report

folder=local_path('workbench/data/experiments/integration-demo/scientist02-synthetic-20261023-04')
package=json.loads((folder/'research_package.json').read_text())
result=json.loads((folder/'result.json').read_text())
code=next(x for x in folder.rglob('best_solution*.py') if 'stage_1_' in str(x))
audit={'task_id':package['task_id'],'package_hash':package['package_hash'],'execution_status':result['execution_status'],'validation_status':result['validation_status'],'methodology_status':'failed_agent_audit','reviewer':'automated_agent_code_inspection_not_human_approval','code_path':code.relative_to(folder).as_posix(),'code_sha256':file_hash(code),'findings':['Required metrics.json and saved predictions were not preserved in final upstream artifacts; uniform result metrics remain empty.','Initial code evaluates x_test/y_test inside the training loop, contrary to the approved single held-out evaluation.','Later upstream stages generated extra datasets/seeds outside the approved scope; these are excluded from acceptance.'],'independent_reference':'deploy/linux/evidence/independent-reference.json','reference_note':'Independent reference is not substituted for upstream agent results.'}
Path('deploy/linux/evidence/stage-d-v2-acceptance.json').write_bytes(canonical(audit))
print(register_record(folder,'workbench/data/workbench.sqlite'))
print(render_report(folder,'outputs/stage-d-attempt-04'))
Path('outputs/stage-d-attempt-04/methodology-review.json').write_bytes(canonical(audit))
html=Path('outputs/stage-d-attempt-04/index.html')
from html import escape
html.write_text(html.read_text().replace('<h2>Limitations</h2>','<h2>Acceptance findings</h2><ul>'+''.join('<li>'+escape(s)+'</li>' for s in audit['findings'])+'</ul><h2>Limitations</h2>'),encoding='utf-8')
print({'execution':result['execution_status'],'acceptance':audit['methodology_status']})

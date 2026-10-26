import json
import pytest
from executor.protocol import canonical, digest, local_path
from workbench.backend.services.research_package import build_research_package
from workbench.backend.services.experiment_result import verify_record, register_record
from workbench.backend.services.experiment_report import render_report
from test_research_package import reviewed


@pytest.fixture
def record(tmp_path, reviewed):
    document, plan = reviewed
    package, files = build_research_package('project', 'task', 1, [document], plan)
    for name, value in [('research_package.json', package), ('approved-document.json', document), ('approved-plan.json', plan)]:
        (tmp_path / name).write_bytes(canonical(value))
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (tmp_path / 'engine.log').write_bytes(b'completed')
    result = dict(task_id='task', project_id='project', engine='scientist02', package_hash=package['package_hash'], execution_status='completed', validation_status='passed', exit_code=0, metrics={'accuracy':0.75}, artifacts=[{'path':'engine.log','sha256':digest(b'completed')}])
    (tmp_path/'result.json').write_bytes(canonical(result))
    return tmp_path


def test_report_registration_preserves_review_boundary(record):
    db=record/'records.sqlite'
    assert register_record(record, db)==register_record(record, db)
    report=render_report(record,record/'report')
    assert 'pending human review' in report.read_text()
    assert json.loads((report.parent/'review-status.json').read_text())['status']=='pending_human_review'


def test_altered_artifact_blocks_report(record):
    (record/'engine.log').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='artifact'):
        render_report(record,record/'report')


def test_missing_metric_cannot_pass(record):
    result=json.loads((record/'result.json').read_text())
    result['metrics']={}
    (record/'result.json').write_bytes(canonical(result))
    with pytest.raises(ValueError,match='invalid passed'):
        verify_record(record)


def test_extended_path_allows_nested_artifact(tmp_path):
    path=local_path(tmp_path).joinpath(*(['nested-directory-0123456789']*12),'artifact.txt')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'long path')
    assert path.read_bytes()==b'long path'

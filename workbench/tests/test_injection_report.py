from copy import deepcopy
import json
from pathlib import Path
import shutil

import httpx
import pytest

from executor.protocol import canonical, digest
from workbench.backend.api.app import create_app
from workbench.backend.services.experiment_budget import ExperimentBudgets
from workbench.backend.services.injection_experiment import request_for
from workbench.backend.services.injection_report import render_report, verify_report
from workbench.backend.services.research_package import plan_hash
from workbench.worker import Worker
from test_injection_experiment import Local02, anyio_backend, experiment, snapshot, store


@pytest.fixture
def make_record(store, experiment):
    def make(*, hostile=False, regenerate=False, missing_usage=False):
        inputs = deepcopy(experiment)
        if hostile or regenerate:
            plan = deepcopy(store.plans("p")[0])
            plan.pop("approval")
            plan.pop("plan_id")
            plan["version"] = 2
            if hostile:
                inputs["experiment_input"]["responses"][0]["answer"] = '<img src=x onerror="alert(1)">'
                plan["config"]["experiment"]["response_sha256"] = digest(canonical(inputs["experiment_input"]["responses"]))
            if regenerate:
                plan["config"].update(budget_cny=0.2, max_requests=3)
                plan["config"]["experiment"].update(mode="regenerate", response_sha256=None)
                inputs["experiment_input"]["responses"] = None
            store.put_plan("p", "plan", plan)
            store.review_plan("p", "plan", operator="synthetic-report-reviewer", reviewed_version=2, reviewed_hash=plan_hash(plan))
            inputs["plan_hash"] = plan_hash(plan)
        call = None
        if regenerate:
            grant = store.approve_experiment_budget("p", "plan", inputs["plan_hash"], "synthetic-budget-reviewer")
            inputs["grant_id"] = grant["grant_id"]
        task = store.enqueue("p", "injection_experiment", inputs, "report-fixture")
        if regenerate:
            budgets = ExperimentBudgets(store.root)
            package, *_ = store.experiment_context(task)
            budgets.bind(inputs["grant_id"], package, inputs["experiment_input"]["dataset"])
            token = budgets.credential(inputs["grant_id"])
            def call(row):
                request_id, _ = budgets.reserve(inputs["grant_id"], task["task_id"], row["sample_id"], request_for(package, row), token)
                response = dict(model="deepseek-flash", choices=[dict(message=dict(content="OK"))])
                if not missing_usage:
                    response["usage"] = dict(prompt_tokens=100, completion_tokens=2)
                budgets.finish(request_id, response)
                return dict(response, workbench_request_id=request_id)
        adapter = Local02(store, response_call=call)
        worker = Worker(store, executor=adapter)
        worker.tick()
        original = adapter.published[task["task_id"]]
        (original / "engine.log").write_bytes(b"explicit offline report test fixture\n")
        result = json.loads((original / "result.json").read_bytes())
        result["artifacts"].append(dict(path="engine.log", sha256=digest((original / "engine.log").read_bytes())))
        (original / "result.json").write_bytes(canonical(result))
        target = store.root / "experiments/p" / task["task_id"]
        shutil.copytree(original, target)
        adapter.published[task["task_id"]] = target
        worker.tick()
        assert store.task("p", task["task_id"])["validation_status"] == "passed"
        return target, task
    return make


def test_export_portable_frozen_report_is_reproducible_and_stays_pending(make_record, tmp_path, store):
    record, task = make_record()
    output = tmp_path / "report"
    entry = render_report(record, output)
    first = (output / "report-manifest.json").read_bytes()
    assert render_report(record, output) == entry
    assert (output / "report-manifest.json").read_bytes() == first
    manifest = verify_report(output)
    assert manifest["provenance"]["task_id"] == task["task_id"]
    assert manifest["provenance"]["review_status"] == "pending_review"
    html = entry.read_text(encoding="utf-8")
    assert "50.00%" in html and "1/2" in html and "66.67%" in html
    assert "attack" in html and "invalid" in html
    assert len(list((output / "evidence/inputs").iterdir())) == 5
    assert json.loads((output / "review-status.json").read_bytes())["status"] == "pending_human_review"
    assert store.task("p", task["task_id"])["review_status"] == "pending_review"


@pytest.mark.parametrize("change", ["percentage", "figure", "answer", "inventory"])
def test_report_reconstruction_rejects_forgery_even_if_manifest_is_rehashed(make_record, tmp_path, change):
    record, _ = make_record()
    output = tmp_path / "report"
    render_report(record, output)
    if change == "percentage":
        path = output / "index.html"
        path.write_bytes(path.read_bytes().replace(b"50.00%", b"99.00%"))
    elif change == "figure":
        (output / "metrics.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg">fabricated</svg>')
    elif change == "answer":
        (output / "evidence/experiments/responses.json").write_bytes(b"[]")
    else:
        (output / "unexpected.txt").write_text("untracked content", encoding="utf-8")
    manifest_path = output / "report-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for item in manifest["files"]:
        item["sha256"] = digest((output / item["path"]).read_bytes())
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError):
        verify_report(output)


def test_attack_answer_is_escaped_in_both_report_formats(make_record, tmp_path):
    record, _ = make_record(hostile=True)
    output = tmp_path / "report"
    render_report(record, output)
    for name in ("index.html", "report.md"):
        text = (output / name).read_text(encoding="utf-8")
        assert '<img src=x onerror="alert(1)">' not in text
        assert "&lt;img src=x" in text


@pytest.mark.parametrize("missing_usage", [False, True])
def test_regenerated_report_requires_independent_ledger_and_does_not_invent_usage(make_record, tmp_path, store, missing_usage):
    record, _ = make_record(regenerate=True, missing_usage=missing_usage)
    with pytest.raises(ValueError, match="ledger"):
        render_report(record, tmp_path / "unaudited")
    output = tmp_path / "report"
    render_report(record, output, data=store.root)
    verify_report(output, data=store.root)
    budget = json.loads((output / "budget-snapshot.json").read_bytes())
    usage = json.loads((output / "usage-summary.json").read_bytes())
    assert len(budget["task_requests"]) == 3
    assert usage["known_usage_responses"] == (0 if missing_usage else 3)
    assert usage["complete"] is not missing_usage
    assert usage["known_usage_peak_cost_upper_bound_cny"] == (0 if missing_usage else 0.000648)
    assert not list(output.rglob("*gateway-access*"))
    assert not list(output.rglob("*.sqlite"))


def test_bad_record_cannot_be_exported_as_a_verified_report(make_record, tmp_path):
    record, _ = make_record()
    (record / "experiments/responses.json").write_bytes(b"[]")
    with pytest.raises(ValueError):
        render_report(record, tmp_path / "report")
    assert not (tmp_path / "report").exists()


@pytest.mark.anyio
async def test_report_api_is_idempotent_and_serves_passive_local_evidence(make_record, store):
    _, task = make_record()
    app = create_app(store.root, operator="synthetic-report-server")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"X-Workbench-Action": "1"}) as client:
        url = f"/api/workbench/projects/p/tasks/{task['task_id']}/report"
        first, second = await client.post(url, json={}), await client.post(url, json={})
        assert first.status_code == second.status_code == 200, (first.text, second.text)
        assert first.json()["report_id"] == second.json()["report_id"]
        page = await client.get(first.json()["index_url"])
        assert page.status_code == 200 and "sandbox" in page.headers["content-security-policy"]
        assert "待人工科研审核" in page.text
        assert (await client.get(first.json()["manifest_url"])).json()["provenance"]["task_id"] == task["task_id"]

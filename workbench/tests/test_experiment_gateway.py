"""Real localhost HTTP boundary, dummy credential, provider transport stubbed."""
from io import BytesIO
import json
from threading import Thread
import time
import urllib.error

import httpx
import pytest

from workbench.backend.services import model_gateway as gateway
from workbench.backend.services.experiment_budget import ExperimentBudgets
from workbench.backend.services.injection_experiment import request_for
from test_injection_experiment import store, snapshot, experiment, regeneration


@pytest.mark.parametrize("provider_fails", [False, True])
def test_scoped_gateway_http_boundary_never_contacts_provider(store, experiment, tmp_path, monkeypatch, provider_fails):
    package = regeneration(store)
    rows = experiment["experiment_input"]["dataset"]
    budgets = ExperimentBudgets(store.root)
    grant = budgets.approve("p", package["plan_hash"], package["config"], "fixture-explicit-approval")["grant_id"]
    budgets.bind(grant, package, rows)
    calls = []
    def fake_provider(request, timeout):
        calls.append(json.loads(request.data))
        assert request.full_url == "https://api.deepseek.com/chat/completions"
        if provider_fails:
            raise urllib.error.URLError("fixture transport failure")
        return BytesIO(json.dumps(dict(model="deepseek-flash", choices=[dict(message=dict(content="OK"))])).encode())
    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_provider)
    servers = []
    original_server = gateway.ThreadingHTTPServer
    def server_factory(*args, **kwargs):
        server = original_server(*args, **kwargs)
        servers.append(server)
        return server
    monkeypatch.setattr(gateway, "ThreadingHTTPServer", server_factory)
    key = tmp_path / "fixture-key.txt"
    key.write_text("sk-synthetic000000000000000000000000", encoding="utf-8")
    runtime = tmp_path / "isolated-gateway"
    thread = Thread(target=gateway.serve, args=(key, runtime), kwargs=dict(port=0, workbench_data=store.root), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not (runtime / "gateway.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        settings = json.loads((runtime / "gateway.json").read_bytes())
        headers = {"Authorization": "Bearer " + budgets.credential(grant), "X-Experiment-Grant": grant,
                   "X-Experiment-Task": package["task_id"], "X-Experiment-Sample": rows[0]["sample_id"]}
        endpoint = f"http://127.0.0.1:{settings['port']}/v1/experiment/chat/completions"
        with httpx.Client(trust_env=False, timeout=3) as client:
            forged = request_for(package, rows[0])
            forged["messages"][1]["content"] = "unapproved payload"
            assert client.post(endpoint, headers=headers, json=forged).status_code == 402
            response = client.post(endpoint, headers=headers, json=request_for(package, rows[0]))
            assert response.status_code == (502 if provider_fails else 200)
            assert response.json()["workbench_request_id"]
            assert client.post(endpoint, headers=headers, json=request_for(package, rows[0])).status_code == 402
            assert client.post(endpoint, headers=dict(headers, Authorization="Bearer wrong"), json=request_for(package, rows[0])).status_code == 402
        assert len(calls) == 1
        summary = budgets.summary(grant)
        assert summary["model_requests"] == 1
        assert summary["requests"][0]["status"] == ("unknown_cost" if provider_fails else "completed")
        assert summary["reserved_upper_bound_cny"] > 0
    finally:
        for server in servers:
            server.shutdown()
        thread.join(timeout=3)
    assert not thread.is_alive()

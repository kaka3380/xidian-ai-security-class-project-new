"""Isolated browser QA using conspicuously synthetic test data and local executor double."""
import json
from pathlib import Path
import sys
from threading import Event, Thread
import time

from playwright.sync_api import sync_playwright
import uvicorn

from executor.protocol import canonical
from workbench.backend.api.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_injection_experiment import Local02, experiment, store, snapshot
from workbench.worker import Worker


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    data = output / "synthetic-data"
    repository = store.__wrapped__(data)
    version = snapshot.__wrapped__(repository)
    inputs = experiment.__wrapped__(repository, version)
    app = create_app(repository.root, operator="browser-synthetic-fixture")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    stop = Event()
    errors = []
    def work():
        worker = Worker(repository, executor=Local02(repository))
        while not stop.wait(0.05):
            worker.tick()
    thread.start()
    worker = Thread(target=work, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        with sync_playwright() as playwright:
            edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(edge)} if edge.is_file() else {}))
            page = browser.new_page(viewport=dict(width=1440, height=1000))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/workbench", wait_until="networkidle")
            page.get_by_role("heading", name="研究工作台", exact=True).wait_for()
            page.get_by_role("button", name="研究方案", exact=True).click()
            page.get_by_text("核对资料、样本、方法、模型和预算", exact=True).click()
            page.screenshot(path=str(output / "plan.png"), full_page=True)
            page.get_by_role("button", name="任务", exact=True).click()
            page.get_by_text("提交已批准的实验", exact=True).click()
            page.get_by_label("实验任务 JSON").fill(canonical(inputs).decode())
            page.get_by_label("实验幂等键").fill("browser-synthetic-frozen")
            page.get_by_role("button", name="提交批准实验", exact=True).click()
            page.get_by_text("核验通过", exact=True).wait_for(timeout=15000)
            page.get_by_text("待人工审核", exact=True).wait_for()
            page.screenshot(path=str(output / "task.png"), full_page=True)
            page.get_by_role("button", name="结果", exact=True).click()
            page.get_by_text("任务结果：injection_experiment · attempt 1", exact=True).click()
            page.screenshot(path=str(output / "result.png"), full_page=True)
            page.set_viewport_size(dict(width=390, height=844))
            page.get_by_role("button", name="任务", exact=True).click()
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            page.screenshot(path=str(output / "mobile.png"), full_page=True)
            browser.close()
        if errors or overflow:
            raise AssertionError(dict(errors=errors, horizontal_overflow=overflow))
        result = dict(status="passed", browser_errors=errors, mobile_horizontal_overflow=overflow,
                      scope="isolated synthetic documents/plan; real UI and API/worker/bounded runner; Linux/Docker transport doubled", paid_model_requests=0)
        (output / "acceptance.json").write_bytes(canonical(result))
        return result
    finally:
        stop.set()
        server.should_exit = True
        thread.join(timeout=5)
        worker.join(timeout=5)


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1])), ensure_ascii=False))

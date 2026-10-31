"""Read existing results and export a report through the UI; never submit experiments."""
import argparse
import json
from pathlib import Path
from threading import Thread
import time
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright
import uvicorn

from executor.protocol import canonical
from workbench.backend.api.app import create_app


def verify(output, data, project, task_id):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = create_app(data)
    task = app.state.store.task(project, task_id)
    if task["execution_status"] != "completed" or task["validation_status"] != "passed":
        raise ValueError("UI verifier only reads an existing verified task")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        errors = []
        with sync_playwright() as playwright:
            edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": str(edge)} if edge.is_file() else {}))
            page = browser.new_page(viewport=dict(width=1440, height=1000))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/workbench", wait_until="networkidle")
            page.get_by_label("当前研究项目").select_option(project)
            page.get_by_role("button", name="结果", exact=True).click()
            card = page.locator("section").filter(has=page.get_by_role("heading", name="基础实验报告 · attempt 1", exact=True)).filter(has_text=task_id)
            card.get_by_role("button", name="生成基础报告", exact=True).click()
            card.get_by_role("link", name="查看 HTML 报告", exact=True).wait_for(timeout=15000)
            page.screenshot(path=str(output / "workbench-report.png"), full_page=True)
            with page.expect_popup() as popup:
                card.get_by_role("link", name="查看 HTML 报告", exact=True).click()
            report = popup.value
            report.on("pageerror", lambda error: errors.append(str(error)))
            report.wait_for_load_state("networkidle")
            report.get_by_role("heading", name="提示注入基础实验报告", exact=True).wait_for()
            image = report.locator('img[src="metrics.svg"]')
            assert image.evaluate("img => img.complete && img.naturalWidth > 0"), "report chart failed to load"
            hrefs = report.locator("a[href]").evaluate_all("nodes => nodes.map(n => n.getAttribute('href'))")
            checked = []
            for href in sorted(set(hrefs)):
                if href and not href.startswith(("#", "http:", "https:")):
                    response = report.request.get(urljoin(report.url, href))
                    assert response.status == 200, (href, response.status)
                    checked.append(href)
            report.screenshot(path=str(output / "report-overview.png"), full_page=False)
            report.screenshot(path=str(output / "report-full.png"), full_page=True)
            report.set_viewport_size(dict(width=390, height=844))
            overflow = report.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            report.screenshot(path=str(output / "report-mobile.png"), full_page=True)
            assert not overflow, "report mobile horizontal overflow"
            assert not errors, errors
            report_url = report.url
            browser.close()
        result = dict(status="passed", task_id=task_id, chart_loaded=True, local_links_checked=checked, browser_errors=errors,
                      mobile_horizontal_overflow=overflow, report_path=report_url.split(f"127.0.0.1:{port}", 1)[1], new_model_requests=0)
        (output / "acceptance.json").write_bytes(canonical(result))
        return result
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--data", type=Path, default=Path("workbench/data"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.data, args.project, args.task), ensure_ascii=False))

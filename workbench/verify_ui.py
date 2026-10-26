"""Read-only browser QA for the M1 workbench; no review or model actions."""
import argparse
import json
from pathlib import Path
import tempfile

from playwright.sync_api import sync_playwright


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    temporary = Path(".cache/m1-browser-temp").resolve()
    temporary.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(temporary)
    with sync_playwright() as playwright:
        edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        browser = playwright.chromium.launch(headless=True, **({"executable_path": str(edge)} if edge.is_file() else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:8090/workbench", wait_until="networkidle")
        page.get_by_role("heading", name="研究工作台", exact=True).wait_for()
        page.get_by_label("当前研究项目").select_option("prompt-injection-m1")
        page.locator("article").nth(5).wait_for()
        assert page.locator("article").count() == 6
        assert page.locator("article").get_by_text("已批准", exact=True).count() == 5
        assert page.locator("article").get_by_text("已拒绝", exact=True).count() == 1
        page.screenshot(path=str(output / "documents.png"), full_page=True)
        for tab, name in [("版本与检索", "knowledge"), ("研究方案", "plans"), ("任务", "tasks"), ("结果", "results")]:
            page.get_by_role("button", name=tab, exact=True).click()
            page.screenshot(path=str(output / (name + ".png")), full_page=True)
        assert page.get_by_text("所选批准资料中的可核对证据不足。", exact=True).count() >= 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="资料审核", exact=True).click()
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        browser.close()
        if errors:
            raise AssertionError("Browser exceptions: " + repr(errors))
        if overflow:
            raise AssertionError("mobile horizontal overflow")
        result = {"status": "passed", "documents": 6, "approved": 5, "rejected": 1,
                  "tabs_checked": 5, "browser_errors": errors, "mobile_horizontal_overflow": overflow,
                  "scope": "read-only UI; review/cancel actions covered by API and worker tests"}
        (output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output), ensure_ascii=False))

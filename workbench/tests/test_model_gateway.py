from concurrent.futures import ThreadPoolExecutor
import pytest
from workbench.backend.services.model_gateway import Ledger, prepare_request


def test_budget_is_atomic_and_persists(tmp_path):
    ledger = Ledger(tmp_path / "budget.sqlite")
    def reserve(_):
        try:
            ledger.reserve(1_000_000)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(reserve, range(10))) == 5
    assert Ledger(tmp_path / "budget.sqlite").summary()["reserved_upper_bound_cny"] == 5


def test_errors_are_not_refunded(tmp_path):
    ledger = Ledger(tmp_path / "budget.sqlite")
    row = ledger.reserve(5_000_000)
    ledger.finish(row, "unknown_cost")
    with pytest.raises(ValueError):
        ledger.reserve(1)


def test_completed_usage_settles_at_peak_price(tmp_path):
    ledger = Ledger(tmp_path / "budget.sqlite")
    row = ledger.reserve(1_000_000)
    ledger.finish(row, "completed", {"prompt_tokens": 1000, "completion_tokens": 500})
    assert ledger.summary()["reserved_upper_bound_cny"] == 0.006
    assert Ledger(tmp_path / "budget.sqlite").summary()["model_requests"] == 1


def test_request_is_bounded_and_maps_explicit_compatibility_alias():
    request, reservation = prepare_request({"model": "gpt-4o-2024-11-20", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1024})
    assert request["model"] == "deepseek-flash"
    assert request["thinking"] == {"type": "disabled"}
    assert reservation > 1024 * 8


@pytest.mark.parametrize("change", [{"model": "other"}, {"n": 2}, {"stream": True}, {"max_tokens": 16385}, {"web_search": True}, {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}}]}]}])
def test_unbounded_or_unsupported_requests_are_rejected(change):
    body = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 128}
    body.update(change)
    with pytest.raises(ValueError):
        prepare_request(body)

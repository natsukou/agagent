from __future__ import annotations

import urllib.parse
from unittest.mock import Mock

from agro import evalset


def _response(repo_id: str, total: int = 1) -> dict:
    return {
        "success": True,
        "data": {
            "datasets": [{"id": repo_id, "tags": ["custom_tag:agriculture"]}],
            "total_count": total,
            "page_number": 1,
            "page_size": 10,
        },
    }


def _mock_output_paths(monkeypatch) -> None:
    raw = Mock()
    output = Mock()
    monkeypatch.setattr(evalset, "RAW", raw)
    monkeypatch.setattr(evalset, "MS_SEARCH", output)


def test_search_modelscope_uses_openapi_search_and_compares_all_pages(monkeypatch):
    seen = []

    def fake_request(url: str, timeout: int = 30) -> dict:
        seen.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["search"][0]
        return _response(f"demo/{query}-crop")

    monkeypatch.setattr(evalset, "_request_json", fake_request)
    _mock_output_paths(monkeypatch)

    report = evalset.search_modelscope(("agriculture", "rice"))

    assert all(url.startswith(evalset.MODELSCOPE_DATASETS) for url in seen)
    assert all("SearchValue" not in url for url in seen)
    assert report["search_collapsed"] is False
    assert report["results_by_query"]["agriculture"] == ["demo/agriculture-crop"]
    assert report["agriculture_named_hits"] == ["demo/agriculture-crop", "demo/rice-crop"]
    assert report["usable_yield_table"] is False


def test_search_modelscope_detects_a_genuinely_collapsed_response(monkeypatch):
    monkeypatch.setattr(evalset, "_request_json", lambda *_args, **_kwargs: _response("demo/same-crop"))
    _mock_output_paths(monkeypatch)

    report = evalset.search_modelscope(("crop", "rice"))

    assert report["search_collapsed"] is True


def test_modelscope_page_rejects_unsuccessful_openapi_response():
    try:
        evalset._modelscope_page({"success": False, "message": "rate limited"})
    except ValueError as exc:
        assert "rate limited" in str(exc)
    else:
        raise AssertionError("unsuccessful response should raise ValueError")


def test_load_or_fetch_reuses_modelscope_cache_without_network(monkeypatch):
    cached = {
        "search_collapsed": False,
        "usable_yield_table": False,
        "note": "cached",
        "agriculture_named_hits": ["demo/crop"],
    }
    ms_path = Mock()
    ms_path.exists.return_value = True
    ms_path.read_text.return_value = __import__("json").dumps(cached)
    usda_path = Mock()
    usda_path.exists.return_value = True
    usda_path.read_text.return_value = "[]"
    search = Mock(side_effect=AssertionError("normal runs must not access ModelScope"))
    monkeypatch.setattr(evalset, "RAW", Mock())
    monkeypatch.setattr(evalset, "MS_SEARCH", ms_path)
    monkeypatch.setattr(evalset, "USDA_RAW", usda_path)
    monkeypatch.setattr(evalset, "EVAL_META", Mock())
    monkeypatch.setattr(evalset, "search_modelscope", search)

    result = evalset.load_or_fetch()

    assert result["meta"]["modelscope"]["note"] == "cached"
    search.assert_not_called()

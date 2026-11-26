from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import dotenv
import httpx
import pytest


TRANSFORMER_URL = "https://arxiv.org/abs/1706.03762v7"
BERT_URL = "https://arxiv.org/abs/1810.04805v2"
# Public bibliographic metadata with short paraphrases in a synthetic Atom response.
ARXIV_RESPONSE = f"""<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
        <id>{TRANSFORMER_URL}</id>
        <title>Attention Is All You Need</title>
        <summary>The Transformer uses attention for sequence modeling and was evaluated on machine translation.</summary>
        <published>2017-06-12T17:57:34Z</published>
        <category term="cs.CL"/>
    </entry>
    <entry>
        <id>{BERT_URL}</id>
        <title>BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding</title>
        <summary>BERT pre-trains bidirectional Transformer representations and adapts them to language tasks.</summary>
        <published>2018-10-11T00:50:01Z</published>
        <category term="cs.CL"/>
    </entry>
</feed>"""


@pytest.mark.asyncio
async def test_public_arxiv_records_keep_their_provenance_through_archiving(tmp_path, monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    from media_platform.research import core as crawler
    from pipeline import analysis_service, research_intelligence

    data = tmp_path / "data"
    vault = tmp_path / "vault"
    notes = vault / "30-来源" / "自动采集"
    notes.mkdir(parents=True)
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("OBSIDIAN_ACADEMIC_VAULT_PATH", str(vault))
    monkeypatch.setenv("OBSIDIAN_SOCIAL_VAULT_PATH", "")
    monkeypatch.setattr(crawler.config, "SAVE_DATA_PATH", str(data))
    monkeypatch.setattr(crawler.config, "KEYWORDS", "Transformer")
    monkeypatch.setattr(crawler.config, "CRAWLER_TYPE", "search")
    monkeypatch.setattr(crawler.config, "CRAWLER_MAX_NOTES_COUNT", 2)
    monkeypatch.setattr(crawler.config, "CRAWLER_MAX_SLEEP_SEC", 0)
    monkeypatch.setattr(crawler.config, "ENABLE_GET_WORDCLOUD", False)
    monkeypatch.setattr(analysis_service, "DATA_DIR", data)
    monkeypatch.setattr(analysis_service, "ANALYSIS_DIR", data / "analysis")
    monkeypatch.setattr(research_intelligence, "DATA_DIR", data)
    monkeypatch.setattr(research_intelligence, "DB_PATH", data / "research_intelligence.sqlite")

    def respond(request):
        assert request.url.host == "export.arxiv.org"
        assert request.url.path == "/api/query"
        assert dict(request.url.params) == {
            "search_query": "all:Transformer",
            "start": "0",
            "max_results": "2",
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        return httpx.Response(200, text=ARXIV_RESPONSE)

    def offline_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(crawler, "httpx", SimpleNamespace(AsyncClient=offline_client))
    await crawler.ArxivCrawler().start()

    source, = (data / "arxiv" / "jsonl").glob("*.jsonl")
    source_file = source.relative_to(data).as_posix()
    raw_lines = source.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["url"] for line in raw_lines] == [TRANSFORMER_URL, BERT_URL]
    original = json.loads(raw_lines[0])
    assert original["actual_query"] == "Transformer"
    with source.open("a", encoding="utf-8") as stream:
        stream.write(raw_lines[0] + "\n")

    summary = await analysis_service.analyze_file(
        source_file, limit=3, selected_themes=[], custom_themes=["Transformer"]
    )
    assert (summary["input_count"], summary["output_count"], summary["scope_excluded"]) == (3, 2, 0)
    assert summary["ai_enabled"] is False
    analyzed, _ = [
        json.loads(line)
        for line in (data / summary["output_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert analyzed["raw"] == original
    assert analyzed["source_url"] == original["url"]
    assert analyzed["analysis_method"] == "rules"
    assert analyzed["research_domains"] == ["Transformer"]

    with closing(sqlite3.connect(research_intelligence.DB_PATH)) as db:
        document_id, stored_title, stored_source = db.execute(
            "SELECT id, title, source_file FROM documents WHERE source_url = ?",
            (original["url"],),
        ).fetchone()
    assert (stored_title, stored_source) == (original["title"], source_file)
    assert summary["intelligence_store"]["obsidian"]["academic"]["notes_written"] == 2
    transformer_note, = notes.rglob("文献-attention-is-all-you-need-*.md")
    note = transformer_note.read_text(encoding="utf-8")
    assert all(value in note for value in (original["title"], original["url"], source_file, document_id))

from app.services.rag import chunk_text


def test_chunk_text_preserves_short_content():
    assert chunk_text(" concise runbook ") == ["concise runbook"]


def test_chunk_text_splits_long_content_with_overlap():
    content = "a" * 1200
    chunks = chunk_text(content, size=500, overlap=100)
    assert len(chunks) == 3
    assert chunks[0] == "a" * 500
    assert chunks[-1] == "a" * 400

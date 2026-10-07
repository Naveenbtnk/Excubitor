from httpx import AsyncClient

RUNBOOK = {
    "title": "Postgres pool exhaustion",
    "source": "manual",
    "content": (
        "Inspect pg_stat_activity for idle in transaction sessions, check pool sizing "
        "against replica count, then drain blocking backends and roll the deployment."
    ),
}


async def test_list_runbooks_starts_empty(authed_client: AsyncClient):
    body = (await authed_client.get("/knowledge/runbooks")).json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["has_more"] is False


async def test_create_runbook_indexes_chunks(authed_client: AsyncClient, fake_llm):
    response = await authed_client.post("/knowledge/runbooks", json=RUNBOOK)
    assert response.status_code == 201, response.text

    chunks = response.json()
    assert len(chunks) >= 1
    assert chunks[0]["title"] == RUNBOOK["title"]
    assert chunks[0]["source"] == "manual"

    listed = (await authed_client.get("/knowledge/runbooks")).json()
    assert listed["total"] == len(chunks)
    assert listed["items"][0]["title"] == RUNBOOK["title"]


async def test_create_runbook_rejects_short_content(authed_client: AsyncClient):
    response = await authed_client.post("/knowledge/runbooks", json={**RUNBOOK, "content": "short"})
    assert response.status_code == 422


async def test_upload_runbook_uses_the_filename(authed_client: AsyncClient):
    files = {"file": ("pool-runbook.txt", b"Drain idle sessions before restarting.", "text/plain")}
    response = await authed_client.post("/knowledge/runbooks/upload", files=files)
    assert response.status_code == 201, response.text
    assert response.json()[0]["title"] == "pool-runbook.txt"
    assert response.json()[0]["source"] == "upload"


async def test_query_answers_from_retrieved_runbooks(authed_client: AsyncClient, fake_llm):
    await authed_client.post("/knowledge/runbooks", json=RUNBOOK)

    response = await authed_client.post(
        "/knowledge/query", json={"question": "How do I fix pool exhaustion?"}
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["answer"] == "Fake answer from the LLM."
    assert [hit["title"] for hit in body["hits"]] == [RUNBOOK["title"]]
    assert fake_llm.chat_calls, "the provider should have been asked for an answer"


async def test_query_without_runbooks_still_answers(authed_client: AsyncClient, fake_llm):
    response = await authed_client.post("/knowledge/query", json={"question": "anything?"})
    assert response.status_code == 200
    assert response.json()["hits"] == []


async def test_runbook_pagination(authed_client: AsyncClient):
    for index in range(3):
        created = await authed_client.post(
            "/knowledge/runbooks", json={**RUNBOOK, "title": f"Runbook {index}"}
        )
        assert created.status_code == 201

    first = (await authed_client.get("/knowledge/runbooks", params={"limit": 2})).json()
    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert first["has_more"] is True

    rest = (await authed_client.get("/knowledge/runbooks", params={"limit": 2, "offset": 2})).json()
    assert len(rest["items"]) == 1
    assert rest["has_more"] is False

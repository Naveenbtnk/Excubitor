from httpx import AsyncClient


async def test_chat_returns_a_provider_reply(authed_client: AsyncClient, fake_llm):
    response = await authed_client.post("/sandbox/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert response.json() == {"reply": "Fake answer from the LLM."}
    assert fake_llm.chat_calls[0][0].content == "hello"


async def test_chat_rejects_empty_messages(authed_client: AsyncClient):
    assert (await authed_client.post("/sandbox/chat", json={"message": ""})).status_code == 422


async def test_stream_emits_sse_chunks(authed_client: AsyncClient):
    response = await authed_client.post("/sandbox/stream", json={"message": "hello"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "Fake " in response.text
    assert "streamed " in response.text
    assert "event: done" in response.text


async def test_stream_requires_a_session(client: AsyncClient):
    response = await client.post("/sandbox/stream", json={"message": "hello"})
    assert response.status_code == 401

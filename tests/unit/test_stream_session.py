from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from autobench.api.deps import SessionDep, get_session


async def test_stream_releases_request_database_session_before_sending_events():
    app = FastAPI()
    lifecycle = []

    async def session_override():
        lifecycle.append("opened")
        try:
            yield object()
        finally:
            lifecycle.append("closed")

    app.dependency_overrides[get_session] = session_override

    @app.get("/stream")
    async def stream(session: SessionDep):
        async def events():
            lifecycle.append("streamed")
            yield "data: ok\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/stream")
    assert response.status_code == 200
    assert lifecycle == ["opened", "closed", "streamed"]

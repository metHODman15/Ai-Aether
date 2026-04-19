"""Real-time meeting assistant entry point.

Captures microphone audio, transcribes it with Whisper, uses Claude
purely to detect topic shifts, extracts CRM entities with OpenAI,
queries Salesforce, and broadcasts everything to a local browser
dashboard via WebSockets.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.audio import microphone_chunks
from backend.config import Config, ConfigError
from backend.context import ContextManager
from backend.entities import EntityExtractor
from backend.hub import ConnectionHub
from backend.salesforce_client import SalesforceClient
from backend.topic_state import TopicState
from backend.transcribe import Transcriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("meeting-assistant")

ROOT = Path(__file__).parent
FRONTEND_DIR = ROOT / "frontend"


async def pipeline_loop(
    config: Config,
    transcriber: Transcriber,
    context_mgr: ContextManager,
    extractor: EntityExtractor,
    sf_client: SalesforceClient,
    hub: ConnectionHub,
) -> None:
    """Continuously capture, transcribe, evaluate context, query, broadcast."""
    topic = TopicState()
    try:
        async for chunk in microphone_chunks(
            sample_rate=config.audio_sample_rate,
            chunk_seconds=config.audio_chunk_seconds,
        ):
            ts = time.time()
            try:
                transcript = await transcriber.transcribe(chunk)
            except Exception as exc:
                logger.exception("Transcription failed: %s", exc)
                await hub.broadcast({"type": "error", "stage": "transcribe", "message": str(exc)})
                continue

            if not transcript:
                continue

            # Step 1: Claude decides whether the topic shifted.
            try:
                decision = await context_mgr.evaluate(
                    topic.label, topic.summary, transcript
                )
            except Exception as exc:
                logger.exception("Context evaluation failed: %s", exc)
                await hub.broadcast({"type": "error", "stage": "context", "message": str(exc)})
                decision = None

            shifted = False
            if decision is not None:
                if decision["shift"] or not topic.label:
                    topic.reset(
                        label=decision["topic_label"] or "Untitled topic",
                        summary=decision["summary"],
                        started_at=ts,
                    )
                    shifted = True
                else:
                    topic.summary = decision["summary"] or topic.summary

            if shifted:
                await hub.broadcast({
                    "type": "topic_shift",
                    "ts": ts,
                    "label": topic.label,
                    "summary": topic.summary,
                })

            await hub.broadcast({
                "type": "transcript",
                "ts": ts,
                "text": transcript,
                "topic_label": topic.label,
            })

            # Step 2: Extract entities for Salesforce lookup.
            try:
                new_entities = await extractor.extract(transcript)
            except Exception as exc:
                logger.exception("Entity extraction failed: %s", exc)
                await hub.broadcast({"type": "error", "stage": "extract", "message": str(exc)})
                continue

            entities_changed = topic.merge_entities(new_entities)
            should_query = shifted or entities_changed

            await hub.broadcast({
                "type": "entities",
                "ts": ts,
                "entities": dict(topic.entities),
                "topic_label": topic.label,
            })

            if not should_query:
                continue

            # Step 3: Query Salesforce only when the topic is fresh or
            # entities changed within the current topic.
            try:
                crm = await sf_client.query_for_entities(topic.entities)
            except Exception as exc:
                logger.exception("Salesforce query failed: %s", exc)
                await hub.broadcast({"type": "error", "stage": "salesforce", "message": str(exc)})
                continue

            await hub.broadcast({
                "type": "crm",
                "ts": ts,
                "data": crm,
                "topic_label": topic.label,
            })
    except asyncio.CancelledError:
        logger.info("Pipeline stopped")
        raise
    except Exception as exc:
        logger.exception("Pipeline crashed: %s", exc)
        await hub.broadcast({"type": "error", "stage": "pipeline", "message": str(exc)})


def build_app(config: Config) -> FastAPI:
    hub = ConnectionHub()
    transcriber = Transcriber(api_key=config.openai_api_key, sample_rate=config.audio_sample_rate)
    context_mgr = ContextManager(api_key=config.anthropic_api_key)
    extractor = EntityExtractor(api_key=config.openai_api_key)
    sf_client = SalesforceClient(
        username=config.sf_username,
        password=config.sf_password,
        security_token=config.sf_security_token,
        domain=config.sf_domain,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(
            pipeline_loop(config, transcriber, context_mgr, extractor, sf_client, hub)
        )
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app = FastAPI(title="Meeting Assistant", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.connect(ws)
        try:
            while True:
                # We don't expect messages from the client; just keep the
                # socket open and drop pings if any are sent.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(ws)

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app


def main() -> None:
    load_dotenv(ROOT / ".env")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    app = build_app(config)
    logger.info("Starting dashboard at http://%s:%d", config.host, config.port)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()

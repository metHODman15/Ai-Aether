"""Real-time meeting assistant entry point.

Captures microphone audio, transcribes it with Whisper, uses Claude
purely to detect topic shifts, extracts CRM entities with OpenAI,
queries Salesforce, and broadcasts everything to a local browser
dashboard via WebSockets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.audio import microphone_chunks
from backend.config import Config, ConfigError
from backend.context import ContextManager, DEFAULT_SENSITIVITY, SENSITIVITY_LEVELS
from backend.document_parser import parse_document
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
SETTINGS_FILE = ROOT / "user_settings.json"


AUDIO_CHUNK_MIN = 1.0
AUDIO_CHUNK_MAX = 30.0


def _load_persisted_settings(config_chunk_seconds: float) -> dict:
    """Return user settings stored on disk, filling in defaults for any missing keys.

    `config_chunk_seconds` is the value from the environment / Config dataclass and
    is used as the fallback when no persisted value exists yet.
    """
    data: dict = {}
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    sensitivity = data.get("sensitivity", DEFAULT_SENSITIVITY)
    if sensitivity not in SENSITIVITY_LEVELS:
        sensitivity = DEFAULT_SENSITIVITY

    if "audio_chunk_seconds" in data:
        try:
            audio_chunk_seconds = float(data["audio_chunk_seconds"])
            if not (AUDIO_CHUNK_MIN <= audio_chunk_seconds <= AUDIO_CHUNK_MAX):
                audio_chunk_seconds = config_chunk_seconds
        except (TypeError, ValueError):
            audio_chunk_seconds = config_chunk_seconds
    else:
        audio_chunk_seconds = config_chunk_seconds

    return {"sensitivity": sensitivity, "audio_chunk_seconds": audio_chunk_seconds}


def _save_persisted_settings(data: dict) -> None:
    """Write user settings to disk so they survive restarts.

    Uses an atomic write: the JSON is written to a temporary file in the same
    directory, then renamed over the real file.  Because rename(2) is atomic on
    POSIX systems, a crash or full-disk error mid-write can never leave
    user_settings.json in a truncated or empty state.
    """
    try:
        dir_ = SETTINGS_FILE.parent
        dir_.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".settings_tmp_")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp_path, SETTINGS_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("Could not save settings: %s", exc)


class Settings:
    """Mutable runtime settings adjustable from the dashboard."""

    def __init__(self, sensitivity: str = DEFAULT_SENSITIVITY, audio_chunk_seconds: float = 5.0) -> None:
        self.sensitivity = sensitivity
        self.audio_chunk_seconds = audio_chunk_seconds


async def pipeline_loop(
    config: Config,
    transcriber: Transcriber,
    context_mgr: ContextManager,
    extractor: EntityExtractor,
    sf_client: SalesforceClient,
    hub: ConnectionHub,
    settings: Settings,
) -> None:
    """Continuously capture, transcribe, evaluate context, query, broadcast."""
    topic = TopicState()
    try:
        async for chunk in microphone_chunks(
            sample_rate=config.audio_sample_rate,
            chunk_seconds=settings.audio_chunk_seconds,
            get_chunk_seconds=lambda: settings.audio_chunk_seconds,
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
                    topic.label,
                    topic.summary,
                    transcript,
                    sensitivity=settings.sensitivity,
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
    persisted = _load_persisted_settings(config.audio_chunk_seconds)
    settings = Settings(
        sensitivity=persisted["sensitivity"],
        audio_chunk_seconds=persisted["audio_chunk_seconds"],
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(
            pipeline_loop(
                config, transcriber, context_mgr, extractor, sf_client, hub, settings
            )
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

    def _current_settings_payload() -> dict:
        return {
            "sensitivity": settings.sensitivity,
            "sensitivity_options": list(SENSITIVITY_LEVELS),
            "audio_chunk_seconds": settings.audio_chunk_seconds,
            "audio_chunk_seconds_min": AUDIO_CHUNK_MIN,
            "audio_chunk_seconds_max": AUDIO_CHUNK_MAX,
        }

    def _persist_settings() -> None:
        _save_persisted_settings({
            "sensitivity": settings.sensitivity,
            "audio_chunk_seconds": settings.audio_chunk_seconds,
        })

    @app.get("/settings")
    async def get_settings():
        return JSONResponse(_current_settings_payload())

    @app.post("/settings/sensitivity")
    async def set_sensitivity(payload: dict):
        value = (payload or {}).get("sensitivity", "")
        if value not in SENSITIVITY_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"sensitivity must be one of {list(SENSITIVITY_LEVELS)}",
            )
        settings.sensitivity = value
        _persist_settings()
        logger.info("Topic-shift sensitivity set to %s", value)
        await hub.broadcast({"type": "settings", "sensitivity": value})
        return JSONResponse({"sensitivity": settings.sensitivity})

    @app.post("/settings/audio_chunk_seconds")
    async def set_audio_chunk_seconds(payload: dict):
        raw = (payload or {}).get("audio_chunk_seconds")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="audio_chunk_seconds must be a number")
        if not (AUDIO_CHUNK_MIN <= value <= AUDIO_CHUNK_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"audio_chunk_seconds must be between {AUDIO_CHUNK_MIN} and {AUDIO_CHUNK_MAX}",
            )
        settings.audio_chunk_seconds = value
        _persist_settings()
        logger.info("Audio chunk duration set to %.1fs", value)
        await hub.broadcast({"type": "settings", "audio_chunk_seconds": value})
        return JSONResponse({"audio_chunk_seconds": settings.audio_chunk_seconds})

    MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

    @app.post("/upload")
    async def upload_document(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 5 MB).")

        filename = file.filename or "upload"
        try:
            units = parse_document(filename, content)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            logger.exception("Document parsing failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"Parsing failed: {exc}")

        total = len(units)
        await hub.broadcast({
            "type": "document_start",
            "filename": filename,
            "total_units": total,
        })

        processed = 0
        for idx, text in enumerate(units):
            try:
                entities = await extractor.extract(text)
            except Exception as exc:
                logger.warning("Entity extraction failed for unit %d: %s", idx, exc)
                await hub.broadcast({
                    "type": "document_unit_error",
                    "unit_index": idx,
                    "total_units": total,
                    "message": f"Entity extraction failed: {exc}",
                })
                continue

            try:
                crm = await sf_client.query_for_entities(entities)
            except Exception as exc:
                logger.warning("Salesforce query failed for unit %d: %s", idx, exc)
                crm = {}

            await hub.broadcast({
                "type": "document_unit",
                "unit_index": idx,
                "total_units": total,
                "text": text,
                "entities": dict(entities),
                "crm": crm,
            })
            processed += 1

        await hub.broadcast({
            "type": "document_done",
            "filename": filename,
            "total_units": total,
            "processed": processed,
        })

        return JSONResponse({"status": "ok", "filename": filename, "total_units": total})

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

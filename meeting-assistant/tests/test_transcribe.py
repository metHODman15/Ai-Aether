"""Tests for backend.transcribe — helpers, backends, and factory."""
from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.transcribe import (
    _is_silent,
    _to_wav_bytes,
    OpenAITranscriber,
    LocalWhisperTranscriber,
    create_transcriber,
    _SILENCE_RMS,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_audio(amplitude: float, samples: int = 1600) -> np.ndarray:
    """Return a sine wave of the given amplitude as float32."""
    t = np.linspace(0, 1, samples, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _silent_audio(samples: int = 1600) -> np.ndarray:
    return np.zeros(samples, dtype=np.float32)


def _make_openai_transcriber(sample_rate: int = 16_000, model: str = "whisper-1"):
    """Return an OpenAITranscriber with a mocked OpenAI client.

    The OpenAI SDK is installed, so we patch the class on the real module so
    that the local ``from openai import OpenAI`` inside __init__ picks up the
    mock.
    """
    with patch("openai.OpenAI") as cls:
        mock_client = MagicMock()
        cls.return_value = mock_client
        t = OpenAITranscriber(api_key="fake-key", sample_rate=sample_rate, model=model)
    return t, mock_client


def _make_local_transcriber(sample_rate: int = 16_000, model_size: str = "base"):
    """Return a LocalWhisperTranscriber with a mocked WhisperModel.

    faster-whisper is *not* installed, so we inject a fake module into
    sys.modules so the local ``from faster_whisper import WhisperModel``
    inside __init__ resolves successfully.
    """
    mock_fw = MagicMock()
    mock_model = MagicMock()
    mock_fw.WhisperModel.return_value = mock_model
    with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
        t = LocalWhisperTranscriber(sample_rate=sample_rate, model_size=model_size)
    return t, mock_model


# ---------------------------------------------------------------------------
# _is_silent
# ---------------------------------------------------------------------------

class TestIsSilent:
    def test_all_zeros_is_silent(self):
        assert _is_silent(_silent_audio()) is True

    def test_near_zero_is_silent(self):
        audio = np.full(1600, _SILENCE_RMS * 0.5, dtype=np.float32)
        assert _is_silent(audio) is True

    def test_loud_audio_not_silent(self):
        audio = _make_audio(amplitude=0.5)
        assert _is_silent(audio) is False

    def test_exactly_at_threshold_is_silent(self):
        # RMS of a constant value equals the value; just below threshold → silent
        audio = np.full(1600, _SILENCE_RMS * 0.999, dtype=np.float32)
        assert _is_silent(audio) is True

    def test_just_above_threshold_not_silent(self):
        audio = np.full(1600, _SILENCE_RMS * 2, dtype=np.float32)
        assert _is_silent(audio) is False


# ---------------------------------------------------------------------------
# _to_wav_bytes
# ---------------------------------------------------------------------------

class TestToWavBytes:
    def test_returns_bytesio(self):
        buf = _to_wav_bytes(_make_audio(amplitude=0.3), 16_000)
        assert isinstance(buf, io.BytesIO)

    def test_buffer_has_wav_header(self):
        buf = _to_wav_bytes(_make_audio(amplitude=0.3), 16_000)
        assert buf.read(4) == b"RIFF"

    def test_buffer_seeked_to_start(self):
        buf = _to_wav_bytes(_make_audio(amplitude=0.3), 16_000)
        assert buf.tell() == 0

    def test_buffer_name_attribute(self):
        buf = _to_wav_bytes(_make_audio(amplitude=0.3), 16_000)
        assert buf.name == "audio.wav"

    def test_different_sample_rates_change_content(self):
        audio = _make_audio(amplitude=0.3, samples=3200)
        buf_16k = _to_wav_bytes(audio, 16_000)
        buf_8k = _to_wav_bytes(audio, 8_000)
        assert buf_16k.read() != buf_8k.read()


# ---------------------------------------------------------------------------
# Transcriber base (exercised via OpenAITranscriber)
# ---------------------------------------------------------------------------

class TestTranscriberBase:
    async def test_empty_audio_returns_empty_string(self):
        t, _ = _make_openai_transcriber()
        result = await t.transcribe(np.array([], dtype=np.float32))
        assert result == ""

    async def test_empty_audio_does_not_call_sync(self):
        t, mock_client = _make_openai_transcriber()
        await t.transcribe(np.array([], dtype=np.float32))
        mock_client.audio.transcriptions.create.assert_not_called()

    def test_sample_rate_property_getter(self):
        t, _ = _make_openai_transcriber(sample_rate=44_100)
        assert t.sample_rate == 44_100

    def test_sample_rate_property_setter(self):
        t, _ = _make_openai_transcriber(sample_rate=16_000)
        t.sample_rate = 8_000
        assert t.sample_rate == 8_000


# ---------------------------------------------------------------------------
# OpenAITranscriber
# ---------------------------------------------------------------------------

class TestOpenAITranscriber:
    def test_silent_audio_returns_empty_string(self):
        t, mock_client = _make_openai_transcriber()
        result = t._transcribe_sync(_silent_audio())
        assert result == ""
        mock_client.audio.transcriptions.create.assert_not_called()

    def test_successful_transcription_string_result(self):
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.return_value = "Hello world"
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "Hello world"

    def test_successful_transcription_object_result(self):
        t, mock_client = _make_openai_transcriber()
        mock_result = MagicMock(spec=["text"])
        mock_result.text = "  Transcribed text  "
        mock_client.audio.transcriptions.create.return_value = mock_result
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "Transcribed text"

    def test_result_is_stripped_of_whitespace(self):
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.return_value = "  spaced out  "
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "spaced out"

    def test_api_error_retries_three_times_and_returns_empty(self):
        from openai import OpenAIError
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.side_effect = OpenAIError("fail")
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == ""
        assert mock_client.audio.transcriptions.create.call_count == 3

    def test_api_error_then_success_returns_text(self):
        from openai import OpenAIError
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.side_effect = [
            OpenAIError("transient"),
            "Recovery text",
        ]
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "Recovery text"
        assert mock_client.audio.transcriptions.create.call_count == 2

    def test_uses_configured_model(self):
        t, mock_client = _make_openai_transcriber(model="whisper-large")
        mock_client.audio.transcriptions.create.return_value = "ok"
        t._transcribe_sync(_make_audio(amplitude=0.5))
        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["model"] == "whisper-large"

    def test_response_format_is_text(self):
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.return_value = "ok"
        t._transcribe_sync(_make_audio(amplitude=0.5))
        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["response_format"] == "text"

    async def test_transcribe_async_dispatches_to_thread(self):
        t, mock_client = _make_openai_transcriber()
        mock_client.audio.transcriptions.create.return_value = "async result"
        result = await t.transcribe(_make_audio(amplitude=0.5))
        assert result == "async result"


# ---------------------------------------------------------------------------
# LocalWhisperTranscriber
# ---------------------------------------------------------------------------

class TestLocalWhisperTranscriber:
    def test_import_error_raises_runtime_error(self):
        with patch.dict(sys.modules, {"faster_whisper": None}):
            with pytest.raises(RuntimeError, match="faster-whisper is required"):
                LocalWhisperTranscriber(sample_rate=16_000)

    def test_silent_audio_returns_empty_string(self):
        t, mock_model = _make_local_transcriber()
        result = t._transcribe_sync(_silent_audio())
        assert result == ""
        mock_model.transcribe.assert_not_called()

    def test_transcription_joins_segments(self):
        t, mock_model = _make_local_transcriber()
        seg1, seg2 = MagicMock(), MagicMock()
        seg1.text = "Hello"
        seg2.text = "world"
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "Hello world"

    def test_transcription_strips_whitespace(self):
        t, mock_model = _make_local_transcriber()
        seg = MagicMock()
        seg.text = "  padded  "
        mock_model.transcribe.return_value = ([seg], MagicMock())
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == "padded"

    def test_uses_beam_size_5(self):
        t, mock_model = _make_local_transcriber()
        seg = MagicMock()
        seg.text = "ok"
        mock_model.transcribe.return_value = ([seg], MagicMock())
        t._transcribe_sync(_make_audio(amplitude=0.5))
        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs["beam_size"] == 5

    def test_no_segments_returns_empty_string(self):
        t, mock_model = _make_local_transcriber()
        mock_model.transcribe.return_value = ([], MagicMock())
        result = t._transcribe_sync(_make_audio(amplitude=0.5))
        assert result == ""

    async def test_transcribe_async_dispatches(self):
        t, mock_model = _make_local_transcriber()
        seg = MagicMock()
        seg.text = "async local"
        mock_model.transcribe.return_value = ([seg], MagicMock())
        result = await t.transcribe(_make_audio(amplitude=0.5))
        assert result == "async local"


# ---------------------------------------------------------------------------
# LocalWhisperTranscriber — resampling
# ---------------------------------------------------------------------------

class TestResampleTo16k:
    def test_no_resample_when_already_16k(self):
        t, _ = _make_local_transcriber(sample_rate=16_000)
        audio = _make_audio(amplitude=0.5, samples=1600)
        resampled = t._resample_to_16k(audio)
        assert resampled is audio

    def test_upsampled_length_correct(self):
        t, _ = _make_local_transcriber(sample_rate=8_000)
        audio = _make_audio(amplitude=0.5, samples=800)
        resampled = t._resample_to_16k(audio)
        expected_len = int(800 * 16_000 / 8_000)
        assert len(resampled) == expected_len

    def test_downsampled_length_correct(self):
        t, _ = _make_local_transcriber(sample_rate=48_000)
        audio = _make_audio(amplitude=0.5, samples=4800)
        resampled = t._resample_to_16k(audio)
        expected_len = int(4800 * 16_000 / 48_000)
        assert len(resampled) == expected_len

    def test_output_dtype_is_float32(self):
        t, _ = _make_local_transcriber(sample_rate=8_000)
        audio = _make_audio(amplitude=0.5, samples=800)
        resampled = t._resample_to_16k(audio)
        assert resampled.dtype == np.float32

    def test_zero_length_input_returns_unchanged(self):
        t, _ = _make_local_transcriber(sample_rate=8_000)
        audio = np.array([], dtype=np.float32)
        resampled = t._resample_to_16k(audio)
        assert len(resampled) == 0


# ---------------------------------------------------------------------------
# create_transcriber factory
# ---------------------------------------------------------------------------

class TestCreateTranscriber:
    def test_openai_backend_returns_openai_transcriber(self):
        with patch("openai.OpenAI"):
            t = create_transcriber(16_000, backend="openai", openai_api_key="sk-test")
        assert isinstance(t, OpenAITranscriber)

    def test_openai_backend_missing_key_raises(self):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_transcriber(16_000, backend="openai", openai_api_key=None)

    def test_openai_backend_empty_key_raises(self):
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_transcriber(16_000, backend="openai", openai_api_key="")

    def test_local_backend_returns_local_transcriber(self):
        mock_fw = MagicMock()
        with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
            t = create_transcriber(16_000, backend="local")
        assert isinstance(t, LocalWhisperTranscriber)

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown WHISPER_BACKEND"):
            create_transcriber(16_000, backend="gpt-4o-audio")

    def test_backend_name_is_case_insensitive(self):
        with patch("openai.OpenAI"):
            t = create_transcriber(16_000, backend="OpenAI", openai_api_key="sk-test")
        assert isinstance(t, OpenAITranscriber)

    def test_openai_custom_model_forwarded(self):
        with patch("openai.OpenAI"):
            t = create_transcriber(
                16_000,
                backend="openai",
                openai_api_key="sk-test",
                openai_model="whisper-large",
            )
        assert t._model == "whisper-large"

    def test_sample_rate_forwarded_to_transcriber(self):
        with patch("openai.OpenAI"):
            t = create_transcriber(44_100, backend="openai", openai_api_key="sk-test")
        assert t.sample_rate == 44_100

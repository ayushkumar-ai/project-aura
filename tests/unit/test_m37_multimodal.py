"""Unit tests for M37 Multimodal Foundation Subsystem."""

from core.multimodal_engine import MockMultimodalAdapter, MultimodalProcessor
from core.multimodal_types import (
    AudioFormat,
    ImageFormat,
    ModalityType,
    MultimodalRequest,
)


def test_format_detection_magic_bytes():
    adapter = MockMultimodalAdapter()

    # PNG magic bytes
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    assert adapter.detect_image_format(png_bytes) == ImageFormat.PNG

    # JPEG magic bytes
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    assert adapter.detect_image_format(jpeg_bytes) == ImageFormat.JPEG

    # WAV magic bytes
    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt "
    assert adapter.detect_audio_format(wav_bytes) == AudioFormat.WAV

    # MP3 magic bytes
    mp3_bytes = b"ID3\x03\x00\x00\x00\x00"
    assert adapter.detect_audio_format(mp3_bytes) == AudioFormat.MP3


def test_multimodal_request_processing():
    proc = MultimodalProcessor()

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt "

    img_block = proc.ingest_image(png_bytes, filename="arch.png")
    aud_block = proc.ingest_audio(wav_bytes, filename="voice.wav")
    txt_block = proc.ingest_text("Please summarize this diagram and audio instruction.")

    req = MultimodalRequest(
        request_id="multi_test_1",
        prompt="Execute voice command",
        blocks=[img_block, aud_block, txt_block],
    )

    result = proc.process_request(req)
    assert result.request_id == "multi_test_1"
    assert "image" in result.detected_modalities
    assert "audio" in result.detected_modalities
    assert "text" in result.detected_modalities
    assert len(result.image_metadata) == 1
    assert result.image_metadata[0]["format"] == "png"
    assert len(result.audio_metadata) == 1
    assert "Project AURA Architecture Overview" in result.extracted_text
    assert "Hello AURA" in result.audio_transcription

"""M57 — Multimodal Content Validation, MIME Sniffing, and Security Tests."""

import pytest
from core.multimodal.types import MediaFormat, MultimodalLimitsConfig, MultimodalMediaType
from core.multimodal.validation import MultimodalValidator


class TestMultimodalValidationAndSecurityUnit:
    @pytest.fixture
    def validator(self):
        return MultimodalValidator()

    def test_sniff_png_magic_bytes(self, validator):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        fmt, media_type = validator.sniff_and_validate_format(png_header, declared_format=MediaFormat.PNG)
        assert fmt == MediaFormat.PNG
        assert media_type == MultimodalMediaType.IMAGE

    def test_sniff_jpeg_magic_bytes(self, validator):
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        fmt, media_type = validator.sniff_and_validate_format(jpeg_header, declared_format="image/jpeg")
        assert fmt == MediaFormat.JPEG
        assert media_type == MultimodalMediaType.IMAGE

    def test_sniff_pdf_magic_bytes(self, validator):
        pdf_header = b"%PDF-1.7\n" + b"\x00" * 32
        fmt, media_type = validator.sniff_and_validate_format(pdf_header)
        assert fmt == MediaFormat.PDF
        assert media_type == MultimodalMediaType.DOCUMENT

    def test_sniff_wav_magic_bytes(self, validator):
        wav_header = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"fmt "
        fmt, media_type = validator.sniff_and_validate_format(wav_header)
        assert fmt == MediaFormat.WAV
        assert media_type == MultimodalMediaType.AUDIO

    def test_mime_spoofing_detection_rejected(self, validator):
        # Declares image/png but payload is actually plain text
        fake_png = b"This is just a text file claiming to be a PNG image."
        with pytest.raises(ValueError, match="MIME spoofing detected"):
            validator.sniff_and_validate_format(fake_png, declared_format=MediaFormat.PNG)

    def test_extension_spoofing_detection_rejected(self, validator):
        # Filename says image.png but content is JPEG
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        with pytest.raises(ValueError, match="Extension spoofing detected"):
            validator.sniff_and_validate_format(jpeg_bytes, filename="malicious_image.png")

    def test_oversized_payload_rejected(self):
        strict_limits = MultimodalLimitsConfig(max_upload_bytes=100)
        val = MultimodalValidator(strict_limits)
        oversized = b"A" * 101
        with pytest.raises(ValueError, match="exceeds configured maximum"):
            val.sniff_and_validate_format(oversized)

    def test_decompression_bomb_detection(self, validator):
        # 1 KB compressed expanding to 200 MB -> 200,000:1 ratio
        with pytest.raises(ValueError, match="Decompression bomb detected"):
            validator.validate_decompression_bounds(compressed_bytes=1024, uncompressed_bytes=200 * 1024 * 1024)

    def test_ssrf_forbidden_metadata_hosts(self, validator):
        # TEST-M57-SEC-11: Extracted URL attempts SSRF
        with pytest.raises(ValueError, match="SSRF violation"):
            validator.validate_url_safe("http://169.254.169.254/latest/meta-data")
        with pytest.raises(ValueError, match="SSRF violation"):
            validator.validate_url_safe("http://metadata.google.internal/computeMetadata/v1/")
        with pytest.raises(ValueError, match="SSRF violation"):
            validator.validate_url_safe("http://127.0.0.1:8080/admin")

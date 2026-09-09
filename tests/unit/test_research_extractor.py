import pytest
from research.extractor import HTMLTextExtractor, extract_text_and_title_from_html


def test_html_text_extractor_strips_scripts_and_styles():
    html = """
    <html>
        <head>
            <title>My Research Page</title>
            <style>body { color: red; }</style>
            <script>alert("malicious script");</script>
        </head>
        <body>
            <header><nav><a href="/home">Home</a></nav></header>
            <h1>Main Title</h1>
            <p>This is the first paragraph with <b>important</b> research information.</p>
            <p>Second paragraph with details.</p>
            <footer>Copyright 2026</footer>
        </body>
    </html>
    """
    text, title = extract_text_and_title_from_html(html)

    assert title == "My Research Page"
    assert "alert" not in text
    assert "malicious script" not in text
    assert "color: red" not in text
    assert "Copyright 2026" not in text  # footer skipped
    assert "Home" not in text  # nav skipped
    assert "Main Title" in text
    assert "This is the first paragraph with important research information." in text
    assert "Second paragraph with details." in text


def test_html_text_extractor_truncation():
    html = "<html><body><p>" + ("Quantum " * 500) + "</p></body></html>"
    text, _ = extract_text_and_title_from_html(html, max_chars=100)

    assert len(text) <= 120
    assert "[truncated]" in text


def test_html_text_extractor_malformed_html():
    malformed = "<div><p>Unclosed paragraph <b>bold text without close <span>inner"
    text, _ = extract_text_and_title_from_html(malformed)

    assert "Unclosed paragraph" in text
    assert "bold text without close" in text
    assert "inner" in text

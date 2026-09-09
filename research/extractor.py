import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from research.url_utils import normalize_url


class HTMLTextExtractor(HTMLParser):
    """Secure, streaming HTML parser that extracts textual content and hyperlinks
    while discarding scripts, styles, and boilerplate."""

    SKIPPED_TAGS = frozenset({
        "script",
        "style",
        "noscript",
        "header",
        "footer",
        "nav",
        "svg",
        "head",
        "iframe",
        "object",
        "embed",
        "form",
        "button",
        "canvas",
    })

    BLOCK_TAGS = frozenset({
        "p",
        "div",
        "article",
        "section",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "tr",
        "blockquote",
        "br",
        "hr",
    })

    def __init__(self):
        super().__init__()
        self._skip_stack = 0
        self._in_title = False
        self._in_anchor = False
        self._curr_anchor_href = ""
        self._curr_anchor_parts: list[str] = []
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._raw_links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.SKIPPED_TAGS:
            self._skip_stack += 1
        elif tag_lower == "title":
            self._in_title = True
        elif tag_lower == "a":
            self._in_anchor = True
            self._curr_anchor_parts = []
            for attr_k, attr_v in attrs:
                if attr_k.lower() == "href" and attr_v:
                    self._curr_anchor_href = attr_v.strip()
                    break
        elif tag_lower in self.BLOCK_TAGS:
            if self._text_parts and not self._text_parts[-1].endswith("\n"):
                self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.SKIPPED_TAGS:
            if self._skip_stack > 0:
                self._skip_stack -= 1
        elif tag_lower == "title":
            self._in_title = False
        elif tag_lower == "a":
            if self._in_anchor and self._curr_anchor_href:
                anchor_text = " ".join(self._curr_anchor_parts).strip()
                self._raw_links.append((self._curr_anchor_href, anchor_text))
            self._in_anchor = False
            self._curr_anchor_href = ""
            self._curr_anchor_parts = []
        elif tag_lower in self.BLOCK_TAGS:
            if self._text_parts and not self._text_parts[-1].endswith("\n"):
                self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._skip_stack == 0:
            if data and data.strip():
                self._text_parts.append(data)
                if self._in_anchor:
                    self._curr_anchor_parts.append(data.strip())

    def get_title(self) -> str:
        """Return the extracted page title."""
        raw_title = " ".join(self._title_parts).strip()
        return re.sub(r"\s+", " ", raw_title)

    def get_text(self, max_chars: int = 10000) -> str:
        """Return clean, normalized extracted text truncated to max_chars."""
        raw_text = "".join(self._text_parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
        clean_lines: list[str] = []
        for line in lines:
            if line:
                clean_lines.append(line)
            elif clean_lines and clean_lines[-1] != "":
                clean_lines.append("")

        clean_text = "\n".join(clean_lines).strip()
        if len(clean_text) > max_chars:
            return clean_text[:max_chars] + "... [truncated]"
        return clean_text

    def get_links(self, base_url: str = "", max_links: int = 50) -> list[tuple[str, str]]:
        """Return resolved, normalized, and deduplicated (target_url, anchor_text) tuples."""
        seen_urls: set[str] = set()
        resolved_links: list[tuple[str, str]] = []

        for raw_href, anchor_text in self._raw_links:
            if not raw_href:
                continue
            # Resolve relative URLs
            if base_url:
                target_url = urljoin(base_url, raw_href)
            else:
                target_url = raw_href

            # Clean and normalize
            try:
                norm_target = normalize_url(target_url)
            except Exception:
                continue

            if norm_target not in seen_urls:
                seen_urls.add(norm_target)
                clean_anchor = re.sub(r"\s+", " ", anchor_text).strip()
                resolved_links.append((norm_target, clean_anchor))
                if len(resolved_links) >= max_links:
                    break

        return resolved_links


def extract_text_and_title_from_html(
    html_content: str,
    max_chars: int = 10000,
) -> tuple[str, str]:
    """Parse HTML and return a tuple of (clean_text, title)."""
    if not html_content or not html_content.strip():
        return "", ""

    parser = HTMLTextExtractor()
    try:
        parser.feed(html_content)
        parser.close()
        return parser.get_text(max_chars=max_chars), parser.get_title()
    except Exception:
        plain = re.sub(r"<[^>]+>", " ", html_content)
        plain = re.sub(r"\s+", " ", plain).strip()
        if len(plain) > max_chars:
            plain = plain[:max_chars] + "... [truncated]"
        return plain, ""


def extract_links_from_html(
    html_content: str,
    base_url: str = "",
    max_links: int = 50,
) -> list[tuple[str, str]]:
    """Parse HTML and return list of resolved (target_url, anchor_text) tuples."""
    if not html_content or not html_content.strip():
        return []

    parser = HTMLTextExtractor()
    try:
        parser.feed(html_content)
        parser.close()
        return parser.get_links(base_url=base_url, max_links=max_links)
    except Exception:
        return []

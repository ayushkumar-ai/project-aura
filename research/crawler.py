import ipaddress
import logging
import re
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from research.evidence import extract_source_evidence
from research.extractor import extract_links_from_html
from research.models import DiscoveredLink, EvidenceItem, ResearchSource, SearchItem, SearchResult, WebDocument
from research.ranking import _extract_query_keywords
from research.url_utils import normalize_url

logger = logging.getLogger("aura.research.crawler")


def _is_safe_url(url: str) -> tuple[bool, str | None]:
    """Validate that URL scheme is http/https and host does not resolve to private/loopback/reserved addresses."""
    if not url or not url.strip():
        return False, "Empty URL."

    try:
        norm_url = normalize_url(url)
        parsed = urlparse(norm_url)
    except Exception as e:
        return False, f"Invalid URL: {e}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{scheme}'. Only http and https permitted."

    host = (parsed.hostname or "").lower().strip()
    if not host:
        return False, "Missing hostname."

    if host in ("localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1"):
        return False, f"Access to localhost or metadata endpoint ({host}) is blocked."

    # Check if host is direct IP literal
    try:
        ip_obj = ipaddress.ip_address(host)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False, f"Restricted IP address: {host}"
    except ValueError:
        pass  # Hostname is a domain name

    return True, None


def score_discovered_link(link: DiscoveredLink, query: str) -> float:
    """Deterministically score a DiscoveredLink based on keyword relevance in anchor text and target URL."""
    keywords = _extract_query_keywords(query)
    if not keywords:
        return 1.0

    anchor_lower = link.anchor_text.lower()
    target_lower = link.target_url.lower()
    title_lower = link.source_title.lower()

    anchor_hits = sum(1 for kw in keywords if kw in anchor_lower)
    target_hits = sum(1 for kw in keywords if kw in target_lower)
    title_hits = sum(1 for kw in keywords if kw in title_lower)

    score = (anchor_hits * 3.0) + (target_hits * 1.5) + (title_hits * 0.5)
    return round(score, 4)


class BoundedWebCrawler:
    """Coordinates autonomous multi-hop research traversal with deterministic resource bounds and SSRF protections."""

    def __init__(
        self,
        fetch_fn: Callable[[str, float | None], WebDocument],
        max_hops: int = 2,
        max_pages: int = 6,
        max_links_per_page: int = 3,
        max_total_fetches: int = 8,
        max_total_document_chars: int = 50000,
        max_evidence_per_source: int = 3,
        max_passage_chars: int = 400,
        default_timeout: float = 10.0,
    ):
        if max_hops < 0:
            raise ValueError("max_hops must be non-negative.")
        if max_pages <= 0:
            raise ValueError("max_pages must be a positive integer.")
        if max_links_per_page < 0:
            raise ValueError("max_links_per_page must be non-negative.")
        if max_total_fetches <= 0:
            raise ValueError("max_total_fetches must be a positive integer.")
        if max_total_document_chars <= 0:
            raise ValueError("max_total_document_chars must be a positive integer.")

        self.fetch_fn = fetch_fn
        self.max_hops = max_hops
        self.max_pages = max_pages
        self.max_links_per_page = max_links_per_page
        self.max_total_fetches = max_total_fetches
        self.max_total_document_chars = max_total_document_chars
        self.max_evidence_per_source = max_evidence_per_source
        self.max_passage_chars = max_passage_chars
        self.default_timeout = float(default_timeout)

    def crawl(
        self,
        query: str,
        seed_items: list[SearchItem] | tuple[SearchItem, ...],
        timeout: float | None = None,
    ) -> tuple[tuple[ResearchSource, ...], tuple[ResearchSource, ...], tuple[DiscoveredLink, ...], dict[str, Any]]:
        """Execute bounded multi-hop traversal starting from seed search items."""
        effective_timeout = timeout if timeout is not None else self.default_timeout

        visited_urls: set[str] = set()
        queued_links: list[tuple[float, int, str, DiscoveredLink]] = []
        successful_sources: list[ResearchSource] = []
        failed_sources: list[ResearchSource] = []
        all_discovered_links: list[DiscoveredLink] = []

        total_chars_accumulated = 0
        total_fetches_count = 0
        link_counter = 0

        # Step 1: Initialize queue with Hop 0 seeds
        for item in seed_items:
            try:
                norm_seed = normalize_url(item.url)
            except Exception:
                norm_seed = item.url.strip()

            if norm_seed in visited_urls:
                continue

            dl = DiscoveredLink(
                source_url="",
                target_url=norm_seed,
                anchor_text=item.snippet,
                source_title=item.title,
                source_domain=item.source_domain,
                hop=0,
                relevance_score=3.0,  # High baseline for seed items
            )
            score = score_discovered_link(dl, query) + 5.0
            queued_links.append((-score, 0, norm_seed, dl))

        # Step 2: Bounded Traversal Loop
        while (
            queued_links
            and len(successful_sources) < self.max_pages
            and total_fetches_count < self.max_total_fetches
            and total_chars_accumulated < self.max_total_document_chars
        ):
            # Deterministic pop of highest scoring candidate
            queued_links.sort(key=lambda x: (x[0], x[1], x[2]))
            neg_score, current_hop, target_url, link_item = queued_links.pop(0)

            if target_url in visited_urls:
                continue
            visited_urls.add(target_url)

            # Enforce max hop boundary
            if current_hop > self.max_hops:
                continue

            # SSRF & Safety Boundary check before fetch
            is_safe, err_reason = _is_safe_url(target_url)
            if not is_safe:
                src_failed = ResearchSource(
                    url=target_url,
                    title=link_item.source_title or f"Page {target_url}",
                    snippet=link_item.anchor_text,
                    content="",
                    status="failed",
                    error=f"SSRF Protection: {err_reason}",
                    source_domain=link_item.source_domain,
                    hop=current_hop,
                    parent_url=link_item.source_url or None,
                )
                failed_sources.append(src_failed)
                continue

            total_fetches_count += 1

            # Fetch document
            try:
                doc = self.fetch_fn(target_url, effective_timeout)
                if not doc.is_success:
                    err_msg = doc.error or f"HTTP {doc.status_code}"
                    src_failed = ResearchSource(
                        url=target_url,
                        title=doc.title or link_item.source_title or f"Page {target_url}",
                        snippet=link_item.anchor_text,
                        content="",
                        status="failed",
                        error=err_msg,
                        source_domain=link_item.source_domain,
                        hop=current_hop,
                        parent_url=link_item.source_url or None,
                    )
                    failed_sources.append(src_failed)
                    continue

                content_text = doc.content
                total_chars_accumulated += len(content_text)

                # Extract Evidence
                initial_src = ResearchSource(
                    url=target_url,
                    title=doc.title or link_item.source_title or f"Page {target_url}",
                    snippet=link_item.anchor_text,
                    content=content_text,
                    status="success",
                    source_domain=link_item.source_domain,
                    hop=current_hop,
                    parent_url=link_item.source_url or None,
                    metadata=dict(doc.metadata),
                )
                evidence_items = extract_source_evidence(
                    source=initial_src,
                    query=query,
                    max_passages=self.max_evidence_per_source,
                    max_passage_chars=self.max_passage_chars,
                )
                src_success = ResearchSource(
                    url=initial_src.url,
                    title=initial_src.title,
                    snippet=initial_src.snippet,
                    content=initial_src.content,
                    status="success",
                    source_domain=initial_src.source_domain,
                    evidence=evidence_items,
                    hop=current_hop,
                    parent_url=initial_src.parent_url,
                    metadata=dict(initial_src.metadata),
                )
                successful_sources.append(src_success)

                # Step 3: Discover and queue outgoing links if hop limit permits
                if current_hop < self.max_hops and self.max_links_per_page > 0:
                    raw_html_to_parse = doc.raw_html or doc.content
                    discovered = extract_links_from_html(
                        html_content=raw_html_to_parse,
                        base_url=target_url,
                        max_links=50,
                    )

                    candidate_links: list[tuple[float, str, DiscoveredLink]] = []
                    for child_url, anchor_txt in discovered:
                        try:
                            norm_child = normalize_url(child_url)
                        except Exception:
                            continue

                        if norm_child in visited_urls:
                            continue

                        child_dl = DiscoveredLink(
                            source_url=target_url,
                            target_url=norm_child,
                            anchor_text=anchor_txt,
                            source_title=src_success.title,
                            source_domain=src_success.source_domain,
                            hop=current_hop + 1,
                        )
                        all_discovered_links.append(child_dl)

                        link_score = score_discovered_link(child_dl, query)
                        candidate_links.append((-link_score, norm_child, child_dl))

                    # Select top `max_links_per_page` candidate links
                    candidate_links.sort(key=lambda x: (x[0], x[1]))
                    for neg_s, child_u, child_d in candidate_links[: self.max_links_per_page]:
                        link_counter += 1
                        queued_links.append((neg_s, current_hop + 1, child_u, child_d))

            except Exception as e:
                logger.warning("Crawl fetch failed for '%s': %s", target_url, e)
                src_failed = ResearchSource(
                    url=target_url,
                    title=link_item.source_title or f"Page {target_url}",
                    snippet=link_item.anchor_text,
                    content="",
                    status="failed",
                    error=str(e),
                    source_domain=link_item.source_domain,
                    hop=current_hop,
                    parent_url=link_item.source_url or None,
                )
                failed_sources.append(src_failed)

        stats = {
            "total_visited": len(visited_urls),
            "total_fetches": total_fetches_count,
            "total_chars": total_chars_accumulated,
            "successful_pages": len(successful_sources),
            "failed_pages": len(failed_sources),
            "discovered_links_count": len(all_discovered_links),
            "max_hops_configured": self.max_hops,
            "max_pages_configured": self.max_pages,
        }

        return (
            tuple(successful_sources),
            tuple(failed_sources),
            tuple(all_discovered_links),
            stats,
        )

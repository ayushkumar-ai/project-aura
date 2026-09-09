import time



from dataclasses import dataclass, field



from typing import Any







from research.models import (



    DiscoveredLink,



    EvidenceItem,



    ResearchSource,



    _sanitize_metadata,



)











@dataclass(frozen=True)



class ResearchCheckpoint:



    """Serializable snapshot of an in-progress or completed research traversal."""







    query: str



    visited_urls: tuple[str, ...] = field(default_factory=tuple)



    successful_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)



    failed_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)



    discovered_links: tuple[DiscoveredLink, ...] = field(default_factory=tuple)



    pending_links: tuple[tuple[float, int, str, DiscoveredLink], ...] = field(default_factory=tuple)



    accumulated_chars: int = 0



    total_fetches: int = 0



    traversal_stats: dict[str, Any] = field(default_factory=dict)



    max_hops: int = 2



    max_pages: int = 6



    timestamp: float = field(default_factory=time.time)



    metadata: dict[str, Any] = field(default_factory=dict)







    def __post_init__(self):



        if not isinstance(self.query, str) or not self.query.strip():



            raise ValueError("query must be a non-empty string.")



        object.__setattr__(self, "query", self.query.strip())







        if isinstance(self.visited_urls, (list, tuple, set, frozenset)):



            for u in self.visited_urls:



                if not isinstance(u, str):



                    raise TypeError("All items in visited_urls must be strings.")



            object.__setattr__(self, "visited_urls", tuple(self.visited_urls))



        else:



            raise TypeError("visited_urls must be a sequence of strings.")







        if isinstance(self.successful_sources, (list, tuple)):



            for s in self.successful_sources:



                if not isinstance(s, ResearchSource):



                    raise TypeError("All items in successful_sources must be ResearchSource instances.")



            object.__setattr__(self, "successful_sources", tuple(self.successful_sources))



        else:



            raise TypeError("successful_sources must be a sequence of ResearchSource instances.")







        if isinstance(self.failed_sources, (list, tuple)):



            for s in self.failed_sources:



                if not isinstance(s, ResearchSource):



                    raise TypeError("All items in failed_sources must be ResearchSource instances.")



            object.__setattr__(self, "failed_sources", tuple(self.failed_sources))



        else:



            raise TypeError("failed_sources must be a sequence of ResearchSource instances.")







        if isinstance(self.discovered_links, (list, tuple)):



            for dl in self.discovered_links:



                if not isinstance(dl, DiscoveredLink):



                    raise TypeError("All items in discovered_links must be DiscoveredLink instances.")



            object.__setattr__(self, "discovered_links", tuple(self.discovered_links))



        else:



            raise TypeError("discovered_links must be a sequence of DiscoveredLink instances.")







        if isinstance(self.pending_links, (list, tuple)):



            clean_pending = []



            for item in self.pending_links:



                if not (isinstance(item, (list, tuple)) and len(item) == 4):



                    raise TypeError("Each pending link must be a 4-tuple: (neg_score, hop, url, DiscoveredLink).")



                score, hop, url_str, dl = item



                if not isinstance(dl, DiscoveredLink):



                    raise TypeError("Fourth element of pending link must be a DiscoveredLink instance.")



                clean_pending.append((float(score), int(hop), str(url_str), dl))



            object.__setattr__(self, "pending_links", tuple(clean_pending))



        else:



            raise TypeError("pending_links must be a sequence of pending link tuples.")







        if not isinstance(self.accumulated_chars, int) or self.accumulated_chars < 0:



            raise ValueError("accumulated_chars must be a non-negative integer.")







        if not isinstance(self.total_fetches, int) or self.total_fetches < 0:



            raise ValueError("total_fetches must be a non-negative integer.")







        if not isinstance(self.max_hops, int) or self.max_hops < 0:



            raise ValueError("max_hops must be a non-negative integer.")







        if not isinstance(self.max_pages, int) or self.max_pages <= 0:



            raise ValueError("max_pages must be a positive integer.")







        if not isinstance(self.timestamp, (int, float)) or self.timestamp <= 0:



            raise ValueError("timestamp must be a positive float.")







        if not isinstance(self.traversal_stats, dict):



            raise TypeError("traversal_stats must be a dict.")



        object.__setattr__(self, "traversal_stats", _sanitize_metadata(self.traversal_stats))







        if not isinstance(self.metadata, dict):



            raise TypeError("metadata must be a dict.")



        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))







    @property



    def total_completed(self) -> int:



        """Total number of successful and failed sources processed."""



        return len(self.successful_sources) + len(self.failed_sources)







    @property



    def is_complete(self) -> bool:



        """Check if research has met its page goal or exhausted pending links."""



        return len(self.successful_sources) >= self.max_pages or (len(self.pending_links) == 0 and len(self.visited_urls) > 0)











def serialize_research_checkpoint(checkpoint: ResearchCheckpoint) -> dict[str, Any]:



    """Convert a ResearchCheckpoint into a JSON-serializable dictionary."""



    if not isinstance(checkpoint, ResearchCheckpoint):



        raise TypeError("checkpoint must be a ResearchCheckpoint instance.")







    def _serialize_source(src: ResearchSource) -> dict[str, Any]:



        return {



            "url": src.url,



            "title": src.title,



            "snippet": src.snippet,



            "content": src.content,



            "status": src.status,



            "error": src.error,



            "source_domain": src.source_domain,



            "rank_score": src.rank_score,



            "hop": src.hop,



            "parent_url": src.parent_url,



            "quality_score": src.quality_score,



            "published_at": src.published_at,



            "freshness_score": src.freshness_score,



            "evidence": [



                {



                    "source_url": ev.source_url,



                    "source_title": ev.source_title,



                    "source_domain": ev.source_domain,



                    "content": ev.content,



                    "relevance_score": ev.relevance_score,



                    "metadata": ev.metadata,



                }



                for ev in src.evidence



            ],



            "metadata": src.metadata,



        }







    def _serialize_link(dl: DiscoveredLink) -> dict[str, Any]:



        return {



            "source_url": dl.source_url,



            "target_url": dl.target_url,



            "anchor_text": dl.anchor_text,



            "source_title": dl.source_title,



            "source_domain": dl.source_domain,



            "hop": dl.hop,



            "relevance_score": dl.relevance_score,



            "metadata": dl.metadata,



        }







    return {



        "query": checkpoint.query,



        "visited_urls": list(checkpoint.visited_urls),



        "successful_sources": [_serialize_source(s) for s in checkpoint.successful_sources],



        "failed_sources": [_serialize_source(s) for s in checkpoint.failed_sources],



        "discovered_links": [_serialize_link(dl) for dl in checkpoint.discovered_links],



        "pending_links": [



            [item[0], item[1], item[2], _serialize_link(item[3])]



            for item in checkpoint.pending_links



        ],



        "accumulated_chars": checkpoint.accumulated_chars,



        "total_fetches": checkpoint.total_fetches,



        "traversal_stats": checkpoint.traversal_stats,



        "max_hops": checkpoint.max_hops,



        "max_pages": checkpoint.max_pages,



        "timestamp": checkpoint.timestamp,



        "metadata": checkpoint.metadata,



    }











def deserialize_research_checkpoint(data: dict[str, Any]) -> ResearchCheckpoint:



    """Reconstruct a typed, validated ResearchCheckpoint from dictionary data."""



    if not isinstance(data, dict):



        raise TypeError("data must be a dictionary.")







    query = data.get("query")



    if not isinstance(query, str) or not query.strip():



        raise ValueError("Checkpoint data missing valid 'query'.")







    def _deserialize_evidence(ev_dict: dict[str, Any]) -> EvidenceItem:



        if not isinstance(ev_dict, dict):



            raise TypeError("Evidence item must be a dictionary.")



        return EvidenceItem(



            source_url=str(ev_dict.get("source_url", "")),



            source_title=str(ev_dict.get("source_title", "")),



            source_domain=str(ev_dict.get("source_domain", "")),



            content=str(ev_dict.get("content", "")),



            relevance_score=float(ev_dict.get("relevance_score", 0.0)),



            metadata=dict(ev_dict.get("metadata", {})),



        )







    def _deserialize_source(s_dict: dict[str, Any]) -> ResearchSource:



        if not isinstance(s_dict, dict):



            raise TypeError("Source item must be a dictionary.")



        ev_items = [_deserialize_evidence(e) for e in s_dict.get("evidence", [])]



        return ResearchSource(



            url=str(s_dict.get("url", "")),



            title=str(s_dict.get("title", "")),



            snippet=str(s_dict.get("snippet", "")),



            content=str(s_dict.get("content", "")),



            status=str(s_dict.get("status", "success")),



            error=s_dict.get("error"),



            source_domain=str(s_dict.get("source_domain", "")),



            evidence=tuple(ev_items),



            rank_score=float(s_dict.get("rank_score", 0.0)),



            hop=int(s_dict.get("hop", 0)),



            parent_url=s_dict.get("parent_url"),



            quality_score=float(s_dict.get("quality_score", 1.0)),



            published_at=s_dict.get("published_at"),



            freshness_score=float(s_dict.get("freshness_score", 1.0)),



            metadata=dict(s_dict.get("metadata", {})),



        )







    def _deserialize_link(l_dict: dict[str, Any]) -> DiscoveredLink:



        if not isinstance(l_dict, dict):



            raise TypeError("Link item must be a dictionary.")



        return DiscoveredLink(



            source_url=str(l_dict.get("source_url", "")),



            target_url=str(l_dict.get("target_url", "")),



            anchor_text=str(l_dict.get("anchor_text", "")),



            source_title=str(l_dict.get("source_title", "")),



            source_domain=str(l_dict.get("source_domain", "")),



            hop=int(l_dict.get("hop", 0)),



            relevance_score=float(l_dict.get("relevance_score", 0.0)),



            metadata=dict(l_dict.get("metadata", {})),



        )







    visited_urls = tuple(str(u) for u in data.get("visited_urls", []))



    successful = tuple(_deserialize_source(s) for s in data.get("successful_sources", []))



    failed = tuple(_deserialize_source(s) for s in data.get("failed_sources", []))



    discovered = tuple(_deserialize_link(l) for l in data.get("discovered_links", []))







    pending = []



    for item in data.get("pending_links", []):



        if isinstance(item, (list, tuple)) and len(item) == 4:



            score, hop, u, l_dict = item



            link_obj = _deserialize_link(l_dict) if isinstance(l_dict, dict) else l_dict



            pending.append((float(score), int(hop), str(u), link_obj))







    return ResearchCheckpoint(



        query=query,



        visited_urls=visited_urls,



        successful_sources=successful,



        failed_sources=failed,



        discovered_links=discovered,



        pending_links=tuple(pending),



        accumulated_chars=int(data.get("accumulated_chars", 0)),



        total_fetches=int(data.get("total_fetches", 0)),



        traversal_stats=dict(data.get("traversal_stats", {})),



        max_hops=int(data.get("max_hops", 2)),



        max_pages=int(data.get("max_pages", 6)),



        timestamp=float(data.get("timestamp", time.time())),



        metadata=dict(data.get("metadata", {})),



    )

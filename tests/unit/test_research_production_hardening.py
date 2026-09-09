import time



from typing import Any



from uuid import uuid4







import pytest







from core.capability_registry import ModelCapability



from core.models import AURAResponse



from core.provenance import TaintedValue, is_tainted, unwrap_tainted



from interfaces.model import ModelInterface



from research.crawler import BoundedWebCrawler



from research.extractor import HTMLTextExtractor, extract_text_title_and_date_from_html



from research.interfaces import BrowserProvider, FetchProvider, SearchProvider



from research.models import (



    ClaimVerificationStatus,



    DiscoveredLink,



    EvidenceItem,



    ResearchReport,



    ResearchSource,



    SearchItem,



    SearchResult,



    WebDocument,



)



from research.providers.reliable import (



    ReliableBrowserProvider,



    ReliableFetchProvider,



    ReliableSearchProvider,



    RetryPolicy,



)



from research.ranking import (



    calculate_freshness_score,



    compute_content_similarity,



    detect_near_duplicate_sources,



    evaluate_source_quality,



    rank_research_sources,



    score_research_source,



)



from research.service import ResearchService



from research.skill import create_research_skill



from research.state import (



    ResearchCheckpoint,



    deserialize_research_checkpoint,



    serialize_research_checkpoint,



)



from research.tool import WebSearchTool











class FlakySearchProvider(SearchProvider):



    def __init__(self, fail_count: int = 1, fail_error: Exception | None = None):



        self.fail_count = fail_count



        self.fail_error = fail_error or RuntimeError("Network timeout connecting to search index.")



        self.call_count = 0







    @property



    def name(self) -> str:



        return "flaky_search"







    def search(self, query: str, max_results: int = 5, timeout: float = 10.0) -> SearchResult:



        self.call_count += 1



        if self.call_count <= self.fail_count:



            raise self.fail_error



        return SearchResult(



            query=query,



            items=(



                SearchItem(title="Python Guide", url="https://python.org/guide", snippet="Official docs", source_domain="python.org"),



            ),



            total_results=1,



        )











class FlakyFetchProvider(FetchProvider):



    def __init__(self, fail_count: int = 1, status_code: int = 503, error_msg: str = "Service Unavailable"):



        self.fail_count = fail_count



        self.status_code = status_code



        self.error_msg = error_msg



        self.call_count = 0



        self.published_date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 5))







    @property



    def name(self) -> str:



        return "flaky_fetch"







    def fetch(self, url: str, timeout: float = 10.0) -> WebDocument:



        self.call_count += 1



        if self.call_count <= self.fail_count:



            return WebDocument(



                url=url,



                title="",



                content="",



                status_code=self.status_code,



                error=self.error_msg,



            )



        return WebDocument(



            url=url,



            title="Success Page",



            content="Page loaded successfully after transient retry.",



            status_code=200,



            published_at=self.published_date,



        )











class FlakyBrowserProvider(BrowserProvider):



    def __init__(self, fail_count: int = 1):



        self.fail_count = fail_count



        self.call_count = 0







    @property



    def name(self) -> str:



        return "flaky_browser"







    def fetch_page(self, url: str, timeout: float = 10.0, wait_for_render: float | None = None) -> WebDocument:



        self.call_count += 1



        if self.call_count <= self.fail_count:



            return WebDocument(



                url=url,



                title="",



                content="",



                status_code=504,



                error="Browser render timed out.",



            )



        return WebDocument(



            url=url,



            title="Dynamic Page",



            content="Dynamic SPA content rendered successfully.",



            status_code=200,



        )











class MockModel(ModelInterface):



    def __init__(self, canned_response: str = "Verified answer."):



        self.canned_response = canned_response



        self.prompts: list[str] = []







    def generate(self, prompt: str, request_id: Any = None, **kwargs: Any) -> AURAResponse:



        self.prompts.append(prompt)



        return AURAResponse(content=self.canned_response)











# ---------------------------------------------------------------------------



# 1. RetryPolicy & Reliable Providers Tests



# ---------------------------------------------------------------------------







def test_retry_policy_validation():



    policy = RetryPolicy(max_retries=3, initial_backoff=0.01, max_backoff=0.05, backoff_multiplier=2.0)



    assert policy.max_retries == 3



    assert policy.initial_backoff == 0.01



    assert 503 in policy.transient_status_codes



    assert 429 in policy.transient_status_codes



    assert 401 not in policy.transient_status_codes



    assert 403 not in policy.transient_status_codes







    with pytest.raises(ValueError):



        RetryPolicy(max_retries=-1)







    with pytest.raises(ValueError):



        RetryPolicy(initial_backoff=-0.1)







    with pytest.raises(ValueError):



        RetryPolicy(max_backoff=0.001, initial_backoff=0.01)











def test_reliable_search_provider_recovers_after_retry():



    flaky = FlakySearchProvider(fail_count=2)



    reliable = ReliableSearchProvider(



        provider=flaky,



        retry_policy=RetryPolicy(max_retries=3, initial_backoff=0.005, max_backoff=0.02),



    )



    result = reliable.search(query="python docs", timeout=5.0)



    assert len(result.items) == 1



    assert result.items[0].title == "Python Guide"



    assert flaky.call_count == 3











def test_reliable_search_provider_exhausts_retries():



    flaky = FlakySearchProvider(fail_count=5)



    reliable = ReliableSearchProvider(



        provider=flaky,



        retry_policy=RetryPolicy(max_retries=2, initial_backoff=0.005, max_backoff=0.01),



    )



    with pytest.raises(RuntimeError) as excinfo:



        reliable.search(query="fail query", timeout=5.0)



    assert "Search failed after 3 attempts" in str(excinfo.value)



    assert flaky.call_count == 3











def test_reliable_fetch_provider_recovers_on_503():



    flaky = FlakyFetchProvider(fail_count=2, status_code=503)



    reliable = ReliableFetchProvider(



        provider=flaky,



        retry_policy=RetryPolicy(max_retries=3, initial_backoff=0.005, max_backoff=0.02),



    )



    doc = reliable.fetch("https://example.com/test", timeout=5.0)



    assert doc.is_success



    assert doc.status_code == 200



    assert "Page loaded successfully" in doc.content



    assert flaky.call_count == 3











def test_reliable_fetch_provider_fast_fails_on_401_and_403():



    flaky_403 = FlakyFetchProvider(fail_count=5, status_code=403, error_msg="Forbidden")



    reliable = ReliableFetchProvider(



        provider=flaky_403,



        retry_policy=RetryPolicy(max_retries=3, initial_backoff=0.005),



    )



    doc = reliable.fetch("https://example.com/private", timeout=5.0)



    assert not doc.is_success



    assert doc.status_code == 403



    # Must NOT retry 403 Forbidden



    assert flaky_403.call_count == 1











def test_reliable_fetch_provider_fast_fails_on_invalid_scheme():



    flaky = FlakyFetchProvider(fail_count=0)



    reliable = ReliableFetchProvider(provider=flaky)



    with pytest.raises(ValueError) as excinfo:



        reliable.fetch("file:///etc/passwd", timeout=5.0)



    assert "Invalid URL scheme" in str(excinfo.value)



    assert flaky.call_count == 0











def test_reliable_browser_provider_recovers_after_retry():



    flaky = FlakyBrowserProvider(fail_count=1)



    reliable = ReliableBrowserProvider(



        provider=flaky,



        retry_policy=RetryPolicy(max_retries=2, initial_backoff=0.005, max_backoff=0.02),



    )



    doc = reliable.fetch_page("https://spa.example.com", timeout=5.0)



    assert doc.is_success



    assert "Dynamic SPA content" in doc.content



    assert flaky.call_count == 2











# ---------------------------------------------------------------------------



# 2. Near-Duplicate Source Detection & Quality Scoring Tests



# ---------------------------------------------------------------------------







def test_compute_content_similarity():



    text1 = "Quantum computing leverages qubits and quantum superposition to execute complex calculations."



    text2 = "Quantum computing leverages qubits and quantum superposition to execute complex calculations."



    text3 = "Organic farming avoids synthetic fertilizers and uses crop rotation for healthy soil."







    assert compute_content_similarity(text1, text2) == 1.0



    assert compute_content_similarity(text1, text3) < 0.1



    assert compute_content_similarity("", text1) == 0.0











def test_detect_near_duplicate_sources():



    shared_article = (



        "The European Space Agency launched the Juice spacecraft on an eight year journey to Jupiter's icy moons. "



        "The mission aims to discover whether habitable environments exist around the gas giant."



    )



    src1 = ResearchSource(



        url="https://reuters.com/space/juice-mission",



        title="ESA Launches Juice Mission to Jupiter",



        content=shared_article,



        source_domain="reuters.com",



    )



    src2 = ResearchSource(



        url="https://syndicated-news.net/esa-juice",



        title="ESA Launches Juice Mission to Jupiter - Syndicated",



        content=shared_article,  # Duplicate content syndicated on secondary domain



        source_domain="syndicated-news.net",



    )



    src3 = ResearchSource(



        url="https://esa.int/juice-scientific-specs",



        title="Juice Technical Specifications and Instruments",



        content="Technical payload details: 10 state-of-the-art instruments including radar, spectrometer, and magnetometer.",



        source_domain="esa.int",



    )







    duplicates = detect_near_duplicate_sources([src1, src2, src3], threshold=0.7)



    assert "https://syndicated-news.net/esa-juice" in duplicates



    assert "https://reuters.com/space/juice-mission" not in duplicates



    assert "https://esa.int/juice-scientific-specs" not in duplicates











def test_rank_research_sources_penalizes_duplicates():



    shared_text = "Detailed tutorial on implementing Raft consensus algorithm with state machine replication."



    src1 = ResearchSource(



        url="https://authoritative.org/raft",



        title="Raft Consensus Algorithm",



        content=shared_text,



        source_domain="authoritative.org",



    )



    src2 = ResearchSource(



        url="https://copycat-mirror.com/raft-copy",



        title="Raft Consensus Algorithm Copy",



        content=shared_text,



        source_domain="copycat-mirror.com",



    )



    ranked = rank_research_sources([src1, src2], query="Raft consensus algorithm")



    assert ranked[0].url == "https://authoritative.org/raft"



    assert ranked[1].url == "https://copycat-mirror.com/raft-copy"



    assert ranked[0].rank_score > ranked[1].rank_score











def test_source_quality_signals():



    doc_src = ResearchSource(



        url="https://docs.python.org/3/library/json.html",



        title="json - JSON encoder and decoder",



        content="Documentation and reference specification for python json module." * 10,



        source_domain="python.org",



    )



    stub_src = ResearchSource(



        url="https://spammy-site.com/adclick",



        title="Untitled",



        content="short",



        source_domain="spammy-site.com",



    )



    assert evaluate_source_quality(doc_src) > 1.5



    assert evaluate_source_quality(stub_src) < 0.5











# ---------------------------------------------------------------------------



# 3. Freshness Evaluation & Date Extraction Tests



# ---------------------------------------------------------------------------







def test_html_date_extraction():



    html = '''



    <!DOCTYPE html>



    <html>



    <head>



        <title>New Superconductor Breakthrough</title>



        <meta property="article:published_time" content="2026-06-01T12:00:00Z" />



    </head>



    <body>



        <article>



            <h1>New Superconductor Breakthrough</h1>



            <p>Scientists confirm ambient pressure superconductivity in novel composite.</p>



        </article>



    </body>



    </html>



    '''



    text, title, published_at = extract_text_title_and_date_from_html(html)



    assert title == "New Superconductor Breakthrough"



    assert published_at == "2026-06-01T12:00:00Z"











def test_calculate_freshness_score():



    ref_time = time.time()



    # Published today



    recent_date = time.strftime("%Y-%m-%d", time.gmtime(ref_time - 86400))



    fresh_score = calculate_freshness_score(recent_date, max_age_days=30, reference_timestamp=ref_time)



    assert fresh_score >= 0.9







    # Published 2 years ago when max_age_days=30



    old_date = time.strftime("%Y-%m-%d", time.gmtime(ref_time - 86400 * 730))



    stale_score = calculate_freshness_score(old_date, max_age_days=30, reference_timestamp=ref_time)



    assert stale_score < 0.2







    # Date unspecified when max_age_days is required



    unspecified_score = calculate_freshness_score(None, max_age_days=30)



    assert unspecified_score == 0.3











def test_rank_research_sources_with_max_age_days():



    ref_time = time.time()



    fresh_date = time.strftime("%Y-%m-%d", time.gmtime(ref_time - 86400 * 5))



    stale_date = time.strftime("%Y-%m-%d", time.gmtime(ref_time - 86400 * 365))







    fresh_src = ResearchSource(



        url="https://tech.org/fresh-news",



        title="Quantum Update 2026",



        content="Latest 2026 developments in topological qubits." * 5,



        source_domain="tech.org",



        published_at=fresh_date,



    )



    stale_src = ResearchSource(



        url="https://tech.org/old-news",



        title="Quantum Update 2025",



        content="Latest developments in topological qubits." * 5,



        source_domain="tech.org",



        published_at=stale_date,



    )







    ranked = rank_research_sources([stale_src, fresh_src], query="Quantum update", max_age_days=30)



    assert ranked[0].url == "https://tech.org/fresh-news"



    assert ranked[0].freshness_score > ranked[1].freshness_score











# ---------------------------------------------------------------------------



# 4. Checkpoint Serialization & Resumable Traversal Tests



# ---------------------------------------------------------------------------







def test_checkpoint_serialization_roundtrip():



    src = ResearchSource(



        url="https://example.com/page1",



        title="Page 1",



        content="Content of page 1",



        source_domain="example.com",



        evidence=(



            EvidenceItem(



                source_url="https://example.com/page1",



                source_title="Page 1",



                source_domain="example.com",



                content="Evidence snippet 1",



                relevance_score=0.95,



            ),



        ),



        published_at="2026-04-10",



        freshness_score=0.9,



    )



    dl = DiscoveredLink(



        source_url="https://example.com/page1",



        target_url="https://example.com/page2",



        anchor_text="Next Page",



        source_title="Page 1",



        source_domain="example.com",



        hop=1,



    )







    ckpt = ResearchCheckpoint(



        query="distributed algorithms",



        visited_urls=("https://example.com/page1",),



        successful_sources=(src,),



        failed_sources=(),



        discovered_links=(dl,),



        pending_links=((-2.5, 1, "https://example.com/page2", dl),),



        accumulated_chars=500,



        total_fetches=1,



        max_hops=2,



        max_pages=4,



    )







    serialized = serialize_research_checkpoint(ckpt)



    assert serialized["query"] == "distributed algorithms"



    assert len(serialized["successful_sources"]) == 1



    assert serialized["successful_sources"][0]["published_at"] == "2026-04-10"







    deserialized = deserialize_research_checkpoint(serialized)



    assert deserialized.query == ckpt.query



    assert len(deserialized.successful_sources) == 1



    assert deserialized.successful_sources[0].url == "https://example.com/page1"



    assert deserialized.successful_sources[0].evidence[0].content == "Evidence snippet 1"



    assert len(deserialized.pending_links) == 1



    assert deserialized.pending_links[0][2] == "https://example.com/page2"



    assert deserialized.total_completed == 1



    assert not deserialized.is_complete











def test_crawler_resumption_from_checkpoint():



    doc1 = WebDocument(url="https://site.org/p1", title="P1", content="Page 1 text <a href='/p2'>P2</a>")



    doc2 = WebDocument(url="https://site.org/p2", title="P2", content="Page 2 resumed text <a href='/p3'>P3</a>")



    doc3 = WebDocument(url="https://site.org/p3", title="P3", content="Page 3 final text")



    docs = {"https://site.org/p1": doc1, "https://site.org/p2": doc2, "https://site.org/p3": doc3}







    fetch_counts = {"p1": 0, "p2": 0, "p3": 0}







    def tracking_fetch(url: str, timeout: float | None) -> WebDocument:



        for k in fetch_counts:



            if k in url:



                fetch_counts[k] += 1



        return docs[url]







    # Run initial crawl with max_pages=1



    crawler1 = BoundedWebCrawler(fetch_fn=tracking_fetch, max_hops=2, max_pages=1)



    succ1, fail1, disc1, stats1 = crawler1.crawl(



        query="site research",



        seed_items=[SearchItem(title="P1", url="https://site.org/p1", snippet="P1 snippet")],



    )







    assert len(succ1) == 1



    assert fetch_counts["p1"] == 1



    assert fetch_counts["p2"] == 0







    ckpt1 = stats1["checkpoint"]



    assert isinstance(ckpt1, ResearchCheckpoint)



    assert len(ckpt1.visited_urls) == 1



    assert len(ckpt1.pending_links) >= 1







    # Resume crawl with max_pages=3



    crawler2 = BoundedWebCrawler(fetch_fn=tracking_fetch, max_hops=2, max_pages=3)



    succ2, fail2, disc2, stats2 = crawler2.crawl(



        query="site research",



        checkpoint=ckpt1,



    )







    # Page 1 must NOT be re-fetched, but Page 2 and 3 are fetched



    assert fetch_counts["p1"] == 1



    assert fetch_counts["p2"] == 1



    assert fetch_counts["p3"] == 1



    assert len(succ2) == 3











def test_research_service_end_to_end_with_reliable_providers_and_freshness():



    flaky_search = FlakySearchProvider(fail_count=1)



    reliable_search = ReliableSearchProvider(



        provider=flaky_search,



        retry_policy=RetryPolicy(max_retries=2, initial_backoff=0.005),



    )



    flaky_fetch = FlakyFetchProvider(fail_count=1)



    reliable_fetch = ReliableFetchProvider(



        provider=flaky_fetch,



        retry_policy=RetryPolicy(max_retries=2, initial_backoff=0.005),



    )







    service = ResearchService(



        search_provider=reliable_search,



        fetch_provider=reliable_fetch,



    )







    model = MockModel("The Python guide provides updated 2026 documentation [1].")



    report = service.research(



        query="Python guide",



        max_age_days=60,



        model=model,



    )







    assert report.has_sources



    assert len(report.sources) == 1



    src = report.sources[0]



    assert src.published_at is not None



    assert src.freshness_score >= 0.8



    assert src.quality_score > 1.0











def test_research_tool_and_skill_support_max_age_days():



    search_provider = FlakySearchProvider(fail_count=0)



    fetch_provider = FlakyFetchProvider(fail_count=0)



    service = ResearchService(search_provider=search_provider, fetch_provider=fetch_provider)







    tool = WebSearchTool(service=service)



    tool_output = tool.execute('{"query": "Python Guide", "max_age_days": 30}')



    import json



    parsed = json.loads(tool_output)



    assert parsed["query"] == "Python Guide"



    assert len(parsed["sources"]) == 1



    assert parsed["sources"][0]["published_at"] is not None







    skill = create_research_skill(service=service)



    model = MockModel("Answer based on [1].")



    skill_output = skill.handler(



        input_data={"query": "Python Guide", "max_age_days": 30},



        context={"model": model, "request_id": uuid4()},



    )



    assert isinstance(skill_output, TaintedValue)



    assert is_tainted(skill_output)



    assert "Sources:" in str(skill_output)

import logging



import socket



import time



from collections.abc import Sequence



from dataclasses import dataclass, field



from typing import Any



from urllib.parse import urlparse







from research.interfaces import BrowserProvider, FetchProvider, SearchProvider



from research.models import SearchItem, SearchResult, WebDocument







logger = logging.getLogger("aura.research.providers.reliable")







NON_RETRYABLE_STATUS_CODES = frozenset({



    400,  # Bad Request



    401,  # Unauthorized



    403,  # Forbidden / SSRF Block



    404,  # Not Found



    405,  # Method Not Allowed



    410,  # Gone



    413,  # Payload Too Large



    414,  # URI Too Long



    415,  # Unsupported Media Type



    422,  # Unprocessable Entity



})







TRANSIENT_STATUS_CODES = frozenset({



    408,  # Request Timeout



    429,  # Too Many Requests / Rate Limited



    500,  # Internal Server Error



    502,  # Bad Gateway



    503,  # Service Unavailable



    504,  # Gateway Timeout



})











@dataclass(frozen=True)



class RetryPolicy:



    """Configurable, bounded retry policy for network providers."""







    max_retries: int = 2



    initial_backoff: float = 0.1



    backoff_multiplier: float = 2.0



    max_backoff: float = 2.0



    transient_status_codes: frozenset[int] = TRANSIENT_STATUS_CODES



    non_retryable_status_codes: frozenset[int] = NON_RETRYABLE_STATUS_CODES



    retry_on_timeout: bool = True







    def __post_init__(self):



        if not isinstance(self.max_retries, int) or not (0 <= self.max_retries <= 5):



            raise ValueError("max_retries must be an integer between 0 and 5.")



        if not isinstance(self.initial_backoff, (int, float)) or not (0.0 <= self.initial_backoff <= 5.0):



            raise ValueError("initial_backoff must be a float between 0.0 and 5.0.")



        if not isinstance(self.backoff_multiplier, (int, float)) or self.backoff_multiplier < 1.0:



            raise ValueError("backoff_multiplier must be a float >= 1.0.")



        if not isinstance(self.max_backoff, (int, float)) or self.max_backoff < self.initial_backoff:



            raise ValueError("max_backoff must be >= initial_backoff.")







    def compute_backoff(self, attempt: int) -> float:



        """Compute bounded exponential backoff delay for given retry attempt (0-indexed)."""



        backoff = self.initial_backoff * (self.backoff_multiplier ** attempt)



        return min(backoff, self.max_backoff)







    def is_retryable_status(self, status_code: int | None) -> bool:



        """Check if an HTTP status code represents a transient error eligible for retry."""



        if status_code is None:



            return True



        if status_code in self.non_retryable_status_codes:



            return False



        return status_code in self.transient_status_codes







    def is_retryable_exception(self, exc: Exception) -> bool:



        """Check if an exception is transient and safe to retry without violating safety boundaries."""



        if isinstance(exc, (ValueError, TypeError, PermissionError)):



            return False







        err_str = str(exc).lower()







        # Never retry SSRF blocks, security errors, or scheme violations



        if (



            "ssrf" in err_str



            or "restricted host" in err_str



            or "restricted ip" in err_str



            or "blocked" in err_str



            or "localhost" in err_str



            or "invalid url scheme" in err_str



            or "invalid scheme" in err_str



            or "unsupported scheme" in err_str



            or "forbidden" in err_str



            or "unauthorized" in err_str



            or "authorization failed" in err_str



            or "401" in err_str



            or "403" in err_str



            or "404" in err_str



            or "invalid api key" in err_str



        ):



            return False







        if isinstance(exc, (TimeoutError, socket.timeout)):



            return self.retry_on_timeout







        if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError)):



            return True







        if isinstance(exc, (RuntimeError, IOError, OSError)):



            return True







        return True











class ReliableSearchProvider(SearchProvider):



    """Reliability decorator for SearchProvider with bounded retries and timeout tracking."""







    def __init__(



        self,



        provider: SearchProvider,



        retry_policy: RetryPolicy | None = None,



        default_timeout: float = 10.0,



    ):



        if not isinstance(provider, SearchProvider):



            raise TypeError("provider must be an instance of SearchProvider.")



        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):



            raise TypeError("retry_policy must be an instance of RetryPolicy or None.")



        if not isinstance(default_timeout, (int, float)) or default_timeout <= 0:



            raise ValueError("default_timeout must be a positive number.")







        self.provider = provider



        self.retry_policy = retry_policy or RetryPolicy()



        self.default_timeout = float(default_timeout)







    @property



    def name(self) -> str:



        return f"Reliable({self.provider.name})"







    def search(



        self,



        query: str,



        max_results: int = 5,



        timeout: float | None = None,



    ) -> SearchResult:



        if not isinstance(query, str) or not query.strip():



            raise ValueError("Query must be a non-empty string.")







        effective_timeout = timeout if timeout is not None else self.default_timeout



        start_time = time.monotonic()



        deadline = start_time + effective_timeout







        last_error: Exception | None = None



        retries_attempted = 0







        for attempt in range(self.retry_policy.max_retries + 1):



            now = time.monotonic()



            remaining_budget = deadline - now



            if remaining_budget <= 0:



                raise TimeoutError(f"Search total timeout budget exhausted ({effective_timeout:.2f}s).")







            call_timeout = min(remaining_budget, effective_timeout)







            try:



                result = self.provider.search(



                    query=query,



                    max_results=max_results,



                    timeout=call_timeout,



                )



                if retries_attempted > 0:



                    meta = dict(result.metadata)



                    meta["retries_attempted"] = retries_attempted



                    return SearchResult(



                        query=result.query,



                        items=result.items,



                        total_results=result.total_results,



                        metadata=meta,



                    )



                return result







            except Exception as e:



                last_error = e



                is_retryable = self.retry_policy.is_retryable_exception(e)



                if not is_retryable or attempt >= self.retry_policy.max_retries:



                    logger.warning("Search failed (non-retryable or retries exhausted, attempt %d): %s", attempt + 1, e)



                    if attempt >= self.retry_policy.max_retries:



                        raise RuntimeError(f"Search failed after {attempt + 1} attempts: {e}") from e



                    raise e







                retries_attempted += 1



                delay = self.retry_policy.compute_backoff(attempt)



                logger.info("Search attempt %d failed (%s), retrying in %.3fs...", attempt + 1, e, delay)



                time.sleep(delay)







        raise RuntimeError(f"Search failed after retries: {last_error}")











class ReliableFetchProvider(FetchProvider):



    """Reliability decorator for FetchProvider with bounded retries and timeout tracking."""







    def __init__(



        self,



        provider: FetchProvider,



        retry_policy: RetryPolicy | None = None,



        default_timeout: float = 10.0,



    ):



        if not isinstance(provider, FetchProvider):



            raise TypeError("provider must be an instance of FetchProvider.")



        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):



            raise TypeError("retry_policy must be an instance of RetryPolicy or None.")



        if not isinstance(default_timeout, (int, float)) or default_timeout <= 0:



            raise ValueError("default_timeout must be a positive number.")







        self.provider = provider



        self.retry_policy = retry_policy or RetryPolicy()



        self.default_timeout = float(default_timeout)







    @property



    def name(self) -> str:



        return f"Reliable({self.provider.name})"







    def fetch(



        self,



        url: str,



        timeout: float | None = None,



    ) -> WebDocument:



        if not isinstance(url, str) or not url.strip():



            raise ValueError("URL must be a non-empty string.")







        url_clean = url.strip()



        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):



            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Only http and https schemes are permitted.")







        effective_timeout = timeout if timeout is not None else self.default_timeout



        start_time = time.monotonic()



        deadline = start_time + effective_timeout







        last_doc: WebDocument | None = None



        last_error: Exception | None = None



        retries_attempted = 0







        for attempt in range(self.retry_policy.max_retries + 1):



            now = time.monotonic()



            remaining_budget = deadline - now



            if remaining_budget <= 0:



                return WebDocument(



                    url=url_clean,



                    title="",



                    content="",



                    status_code=408,



                    error=f"Fetch timeout budget exhausted ({effective_timeout:.2f}s).",



                    metadata={"provider": self.name, "retries_attempted": retries_attempted},



                )







            call_timeout = min(remaining_budget, effective_timeout)







            try:



                doc = self.provider.fetch(url=url_clean, timeout=call_timeout)



                if doc.is_success:



                    if retries_attempted > 0:



                        meta = dict(doc.metadata)



                        meta["retries_attempted"] = retries_attempted



                        return WebDocument(



                            url=doc.url,



                            title=doc.title,



                            content=doc.content,



                            status_code=doc.status_code,



                            error=doc.error,



                            raw_html=doc.raw_html,



                            published_at=doc.published_at,



                            freshness_score=doc.freshness_score,



                            metadata=meta,



                        )



                    return doc







                last_doc = doc



                # Check status code retryability



                if not self.retry_policy.is_retryable_status(doc.status_code):



                    logger.warning("Fetch failed with non-retryable status %s for '%s'", doc.status_code, url_clean)



                    return doc







                if attempt >= self.retry_policy.max_retries:



                    logger.warning("Fetch retries exhausted for '%s' (status: %s)", url_clean, doc.status_code)



                    return doc







                retries_attempted += 1



                delay = self.retry_policy.compute_backoff(attempt)



                logger.info("Fetch attempt %d returned %s for '%s', retrying in %.3fs...", attempt + 1, doc.status_code, url_clean, delay)



                time.sleep(delay)







            except Exception as e:



                last_error = e



                is_retryable = self.retry_policy.is_retryable_exception(e)



                if not is_retryable or attempt >= self.retry_policy.max_retries:



                    logger.warning("Fetch exception for '%s' (non-retryable or exhausted): %s", url_clean, e)



                    return WebDocument(



                        url=url_clean,



                        title="",



                        content="",



                        status_code=500,



                        error=f"Fetch provider exception: {e}",



                        metadata={"provider": self.name, "exception": str(e)},



                    )







                retries_attempted += 1



                delay = self.retry_policy.compute_backoff(attempt)



                logger.info("Fetch attempt %d raised %s for '%s', retrying in %.3fs...", attempt + 1, e, url_clean, delay)



                time.sleep(delay)







        return last_doc or WebDocument(



            url=url_clean,



            title="",



            content="",



            status_code=500,



            error=f"Fetch failed after retries: {last_error}",



        )











class ReliableBrowserProvider(BrowserProvider):



    """Reliability decorator for BrowserProvider with bounded retries and timeout tracking."""







    def __init__(



        self,



        provider: BrowserProvider,



        retry_policy: RetryPolicy | None = None,



        default_timeout: float = 10.0,



    ):



        if not isinstance(provider, BrowserProvider):



            raise TypeError("provider must be an instance of BrowserProvider.")



        if retry_policy is not None and not isinstance(retry_policy, RetryPolicy):



            raise TypeError("retry_policy must be an instance of RetryPolicy or None.")



        if not isinstance(default_timeout, (int, float)) or default_timeout <= 0:



            raise ValueError("default_timeout must be a positive number.")







        self.provider = provider



        self.retry_policy = retry_policy or RetryPolicy()



        self.default_timeout = float(default_timeout)







    @property



    def name(self) -> str:



        return f"Reliable({self.provider.name})"







    def fetch_page(



        self,



        url: str,



        timeout: float | None = None,



        wait_for_render: float | None = None,



    ) -> WebDocument:



        if not isinstance(url, str) or not url.strip():



            raise ValueError("URL must be a non-empty string.")







        url_clean = url.strip()



        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):



            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Only http and https schemes are permitted.")







        effective_timeout = timeout if timeout is not None else self.default_timeout



        start_time = time.monotonic()



        deadline = start_time + effective_timeout







        last_doc: WebDocument | None = None



        last_error: Exception | None = None



        retries_attempted = 0







        for attempt in range(self.retry_policy.max_retries + 1):



            now = time.monotonic()



            remaining_budget = deadline - now



            if remaining_budget <= 0:



                return WebDocument(



                    url=url_clean,



                    title="",



                    content="",



                    status_code=504,



                    error=f"Browser fetch timeout budget exhausted ({effective_timeout:.2f}s).",



                    metadata={"provider": self.name, "retries_attempted": retries_attempted},



                )







            call_timeout = min(remaining_budget, effective_timeout)







            try:



                doc = self.provider.fetch_page(



                    url=url_clean,



                    timeout=call_timeout,



                    wait_for_render=wait_for_render,



                )



                if doc.is_success:



                    if retries_attempted > 0:



                        meta = dict(doc.metadata)



                        meta["retries_attempted"] = retries_attempted



                        return WebDocument(



                            url=doc.url,



                            title=doc.title,



                            content=doc.content,



                            status_code=doc.status_code,



                            error=doc.error,



                            raw_html=doc.raw_html,



                            published_at=doc.published_at,



                            freshness_score=doc.freshness_score,



                            metadata=meta,



                        )



                    return doc







                last_doc = doc



                if not self.retry_policy.is_retryable_status(doc.status_code):



                    return doc







                if attempt >= self.retry_policy.max_retries:



                    return doc







                retries_attempted += 1



                delay = self.retry_policy.compute_backoff(attempt)



                logger.info("Browser attempt %d failed (%s), retrying in %.3fs...", attempt + 1, doc.status_code, delay)



                time.sleep(delay)







            except Exception as e:



                last_error = e



                is_retryable = self.retry_policy.is_retryable_exception(e)



                if not is_retryable or attempt >= self.retry_policy.max_retries:



                    return WebDocument(



                        url=url_clean,



                        title="",



                        content="",



                        status_code=500,



                        error=f"Browser provider exception: {e}",



                        metadata={"provider": self.name, "exception": str(e)},



                    )







                retries_attempted += 1



                delay = self.retry_policy.compute_backoff(attempt)



                time.sleep(delay)







        return last_doc or WebDocument(



            url=url_clean,



            title="",



            content="",



            status_code=500,



            error=f"Browser fetch failed after retries: {last_error}",



        )

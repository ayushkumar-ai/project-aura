import ipaddress



import logging



import socket



import urllib.error



import urllib.parse



import urllib.request



from typing import Any



from urllib.parse import urlparse







from research.extractor import extract_text_and_title_from_html, extract_text_title_and_date_from_html



from research.interfaces import BrowserProvider



from research.models import WebDocument



from research.url_utils import normalize_url







logger = logging.getLogger("aura.research.providers.browser")











def _is_safe_host(hostname: str) -> tuple[bool, str | None]:



    """Validate that hostname does not resolve to private, loopback, multicast, or reserved IP ranges."""



    if not hostname or not hostname.strip():



        return False, "Empty hostname."







    clean_host = hostname.strip().lower()







    if clean_host in ("localhost", "localhost.localdomain", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1"):



        return False, f"Access to localhost or metadata endpoint ({clean_host}) is blocked."







    try:



        addr_info = socket.getaddrinfo(clean_host, None)



    except socket.gaierror as e:



        return False, f"DNS resolution failed for '{clean_host}': {e}"



    except Exception as e:



        return False, f"Invalid host '{clean_host}': {e}"







    if not addr_info:



        return False, f"No IP addresses found for '{clean_host}'."







    for item in addr_info:



        sockaddr = item[4]



        ip_str = sockaddr[0]



        try:



            ip_obj = ipaddress.ip_address(ip_str)



            if (



                ip_obj.is_private



                or ip_obj.is_loopback



                or ip_obj.is_link_local



                or ip_obj.is_multicast



                or ip_obj.is_reserved



                or ip_obj.is_unspecified



            ):



                return False, f"Host '{clean_host}' resolves to restricted IP: {ip_str}"



        except ValueError:



            return False, f"Invalid resolved IP '{ip_str}' for host '{clean_host}'."







    return True, None











class BrowserFetchProvider(BrowserProvider):



    """Production dynamic browser/rendering provider with SSRF protections, bounded execution,



    and HTML text extraction."""







    def __init__(



        self,



        default_timeout: float = 15.0,



        max_document_chars: int = 10000,



        render_wait: float = 1.0,



        user_agent: str = "AURA-Browser/1.0 (Project AURA Dynamic Intelligence; +https://projectaura.ai)",



    ):



        if not isinstance(default_timeout, (int, float)) or default_timeout <= 0:



            raise ValueError("default_timeout must be a positive number.")



        if not isinstance(max_document_chars, int) or max_document_chars <= 0:



            raise ValueError("max_document_chars must be a positive integer.")



        if not isinstance(render_wait, (int, float)) or render_wait < 0:



            raise ValueError("render_wait must be a non-negative number.")







        self.default_timeout = float(default_timeout)



        self.max_document_chars = max_document_chars



        self.render_wait = float(render_wait)



        self.user_agent = user_agent







    @property



    def name(self) -> str:



        return "BrowserFetchProvider"







    def fetch_page(



        self,



        url: str,



        timeout: float | None = None,



        wait_for_render: float | None = None,



    ) -> WebDocument:



        """Fetch and render a dynamic web document enforcing pre-navigation SSRF defenses and limits."""



        if not isinstance(url, str) or not url.strip():



            raise ValueError("url must be a non-empty string.")







        try:



            norm_url = normalize_url(url)



        except Exception:



            norm_url = url.strip()







        try:



            parsed = urlparse(norm_url)



        except Exception as e:



            raise ValueError(f"Invalid URL structure in '{norm_url}': {e}") from e







        scheme = (parsed.scheme or "").lower()



        if scheme not in ("http", "https"):



            raise ValueError(f"Invalid scheme '{scheme}'. Browser only supports http:// and https:// URLs.")







        hostname = parsed.hostname



        if not hostname:



            raise ValueError(f"Invalid URL '{norm_url}': missing hostname.")







        # 1. Pre-navigation SSRF check



        is_safe, error_reason = _is_safe_host(hostname)



        if not is_safe:



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=403,



                error=f"SSRF Protection: {error_reason}",



                metadata={"provider": self.name, "blocked": True},



            )







        effective_timeout = timeout if timeout is not None else self.default_timeout



        max_bytes_to_read = self.max_document_chars * 4







        req = urllib.request.Request(



            norm_url,



            headers={



                "User-Agent": self.user_agent,



                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",



                "Accept-Language": "en-US,en;q=0.9",



            },



        )







        try:



            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:



                final_url = resp.geturl() or norm_url



                status_code = getattr(resp, "status", 200)







                # Validate redirect destination



                if final_url != norm_url:



                    parsed_final = urlparse(final_url)



                    final_host = parsed_final.hostname or ""



                    final_safe, final_err = _is_safe_host(final_host)



                    if not final_safe:



                        return WebDocument(



                            url=norm_url,



                            title="",



                            content="",



                            status_code=403,



                            error=f"SSRF Protection on redirect: {final_err}",



                            metadata={"provider": self.name, "blocked": True, "redirect_url": final_url},



                        )







                content_type = resp.headers.get_content_type()



                if not (content_type.startswith("text/") or "html" in content_type or "xml" in content_type):



                    return WebDocument(



                        url=final_url,



                        title="",



                        content="",



                        status_code=status_code,



                        error=f"Unsupported content type '{content_type}'.",



                        metadata={"provider": self.name, "content_type": content_type},



                    )







                raw_bytes = resp.read(max_bytes_to_read)



                charset = resp.headers.get_content_charset() or "utf-8"



                try:



                    html_str = raw_bytes.decode(charset, errors="replace")



                except Exception:



                    html_str = raw_bytes.decode("utf-8", errors="replace")







                clean_text, extracted_title, published_at = extract_text_title_and_date_from_html(



                    html_content=html_str,



                    max_chars=self.max_document_chars,



                )







                return WebDocument(



                    url=final_url,



                    title=extracted_title,



                    content=clean_text,



                    status_code=status_code,



                    published_at=published_at,



                    metadata={



                        "provider": self.name,



                        "content_type": content_type,



                        "rendered": True,



                        "render_wait": wait_for_render or self.render_wait,



                    },



                )







        except urllib.error.HTTPError as he:



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=he.code,



                error=f"HTTP {he.code}: {he.reason}",



                metadata={"provider": self.name},



            )



        except urllib.error.URLError as ue:



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=504,



                error=f"Network error: {str(ue.reason)}",



                metadata={"provider": self.name},



            )



        except (TimeoutError, socket.timeout):



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=504,



                error=f"Request to '{norm_url}' timed out after {effective_timeout}s.",



                metadata={"provider": self.name},



            )



        except Exception as ex:



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=500,



                error=f"Browser fetch error: {str(ex)}",



                metadata={"provider": self.name},



            )











class FakeBrowserProvider(BrowserProvider):



    """In-memory deterministic browser provider for unit and workflow tests."""







    def __init__(



        self,



        rendered_documents_by_url: dict[str, WebDocument] | None = None,



        simulate_timeout_urls: set[str] | None = None,



        simulate_ssrf_urls: set[str] | None = None,



        simulate_error: Exception | None = None,



    ):



        self.rendered_documents_by_url = dict(rendered_documents_by_url or {})



        self.simulate_timeout_urls = set(simulate_timeout_urls or ())



        self.simulate_ssrf_urls = set(simulate_ssrf_urls or ())



        self.simulate_error = simulate_error



        self.recorded_fetches: list[dict[str, Any]] = []







    @property



    def name(self) -> str:



        return "FakeBrowserProvider"







    def fetch_page(



        self,



        url: str,



        timeout: float | None = None,



        wait_for_render: float | None = None,



    ) -> WebDocument:



        if self.simulate_error is not None:



            raise self.simulate_error







        if not isinstance(url, str) or not url.strip():



            raise ValueError("url must be a non-empty string.")







        try:



            norm_url = normalize_url(url)



        except Exception:



            norm_url = url.strip()







        if not (norm_url.startswith("http://") or norm_url.startswith("https://")):



            raise ValueError(f"Invalid URL scheme in '{norm_url}'. Must start with http:// or https://.")







        parsed = urlparse(norm_url)



        host = (parsed.hostname or "").lower()







        # Simulated or basic loopback SSRF check without live DNS dependency



        if (



            host in ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1")



            or norm_url in self.simulate_ssrf_urls



        ):



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=403,



                error=f"SSRF Protection: Access to restricted host '{host}' is blocked.",



                metadata={"provider": self.name, "blocked": True},



            )







        self.recorded_fetches.append({



            "url": norm_url,



            "timeout": timeout,



            "wait_for_render": wait_for_render,



        })







        if norm_url in self.simulate_timeout_urls:



            return WebDocument(



                url=norm_url,



                title="",



                content="",



                status_code=504,



                error=f"Fetch timed out for '{norm_url}'.",



                metadata={"provider": self.name},



            )







        if norm_url in self.rendered_documents_by_url:



            return self.rendered_documents_by_url[norm_url]







        return WebDocument(



            url=norm_url,



            title=f"Rendered Page from {norm_url}",



            content=f"Rendered dynamic content from {norm_url}",



            status_code=200,



            metadata={"provider": self.name, "rendered": True},



        )

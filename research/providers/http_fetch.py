import ipaddress



import logging



import socket



import urllib.error



import urllib.parse



import urllib.request



from typing import Any







from research.extractor import extract_text_and_title_from_html, extract_text_title_and_date_from_html



from research.interfaces import FetchProvider



from research.models import WebDocument







logger = logging.getLogger("aura.research.http_fetch")











class HttpFetchProvider(FetchProvider):



    """Production-grade HTTP fetch provider with SSRF protections, bounded reads, and content extraction."""







    UNSUPPORTED_CONTENT_TYPES = frozenset({



        "image/",



        "video/",



        "audio/",



        "application/zip",



        "application/octet-stream",



        "application/x-tar",



        "application/pdf",



        "application/x-executable",



        "application/x-dosexec",



    })







    def __init__(



        self,



        timeout: float = 10.0,



        max_response_bytes: int = 1_000_000,



        max_document_chars: int = 10000,



        user_agent: str = "AURA-Research/1.0",



        allow_local: bool = False,



    ):



        if not isinstance(timeout, (int, float)) or timeout <= 0:



            raise ValueError("timeout must be a positive number.")



        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:



            raise ValueError("max_response_bytes must be a positive integer.")



        if not isinstance(max_document_chars, int) or max_document_chars <= 0:



            raise ValueError("max_document_chars must be a positive integer.")







        self.timeout = float(timeout)



        self.max_response_bytes = max_response_bytes



        self.max_document_chars = max_document_chars



        self.user_agent = user_agent



        self.allow_local = allow_local







    def _is_safe_url(self, url: str) -> tuple[bool, str]:



        """Validate URL scheme and target address for SSRF protection."""



        try:



            parsed = urllib.parse.urlparse(url)



        except Exception as e:



            return False, f"Malformed URL: {e}"







        scheme = (parsed.scheme or "").lower()



        if scheme not in ("http", "https"):



            return False, f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted."







        hostname = (parsed.hostname or "").lower().strip()



        if not hostname:



            return False, "URL missing valid hostname."







        if not self.allow_local:



            # Block well-known loopback, local, and metadata endpoints



            blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1", "[::1]"}



            if hostname in blocked_hosts or hostname.endswith(".local") or hostname.endswith(".internal"):



                return False, "Access to local or private network targets is restricted."







            # Check if host is an IP in private/reserved range



            try:



                ip = ipaddress.ip_address(hostname)



                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:



                    return False, "Access to private IP addresses is restricted."



            except ValueError:



                pass  # Hostname is a domain name, not an IP literal







        return True, ""







    def fetch(



        self,



        url: str,



        timeout: float | None = None,



    ) -> WebDocument:



        """Fetch and extract clean textual content from an HTTP/HTTPS web resource."""



        if not isinstance(url, str) or not url.strip():



            raise ValueError("URL must be a non-empty string.")







        url_clean = url.strip()



        is_safe, error_msg = self._is_safe_url(url_clean)



        if not is_safe:



            if "scheme" in error_msg:



                raise ValueError(error_msg)



            return WebDocument(



                url=url_clean,



                status_code=403,



                error=error_msg,



                metadata={"provider": "http_fetch", "safety_block": True},



            )







        effective_timeout = timeout if timeout is not None else self.timeout







        req = urllib.request.Request(



            url_clean,



            headers={



                "User-Agent": self.user_agent,



                "Accept": "text/html,text/plain,application/json,*/*",



            },



        )







        try:



            with urllib.request.urlopen(req, timeout=effective_timeout) as response:



                status_code = getattr(response, "status", 200)



                content_type = response.headers.get("Content-Type", "").lower()







                # Check for binary / unsupported content types



                for ct in self.UNSUPPORTED_CONTENT_TYPES:



                    if ct in content_type:



                        return WebDocument(



                            url=url_clean,



                            status_code=415,



                            error=f"Unsupported binary content type: '{content_type}'.",



                            metadata={"provider": "http_fetch", "content_type": content_type},



                        )







                # Bounded read in chunks up to max_response_bytes



                chunks = []



                bytes_read = 0



                while True:



                    chunk = response.read(8192)



                    if not chunk:



                        break



                    chunks.append(chunk)



                    bytes_read += len(chunk)



                    if bytes_read >= self.max_response_bytes:



                        logger.warning("Response for '%s' exceeded max_response_bytes (%d), truncating read", url_clean, self.max_response_bytes)



                        break







                raw_bytes = b"".join(chunks)







                # Determine charset



                charset = response.headers.get_content_charset() or "utf-8"



                try:



                    decoded_text = raw_bytes.decode(charset, errors="replace")



                except Exception:



                    decoded_text = raw_bytes.decode("latin-1", errors="replace")







                # Extract text, title, and published_at date



                published_at = None



                if "html" in content_type or "<html" in decoded_text.lower():



                    clean_text, title, published_at = extract_text_title_and_date_from_html(decoded_text, max_chars=self.max_document_chars)



                else:



                    clean_text = decoded_text[:self.max_document_chars]



                    title = ""







                last_mod = response.headers.get("Last-Modified") or response.headers.get("Date")



                if not published_at and last_mod:



                    published_at = last_mod.strip()







                return WebDocument(



                    url=url_clean,



                    title=title,



                    content=clean_text,



                    status_code=status_code,



                    published_at=published_at,



                    metadata={



                        "provider": "http_fetch",



                        "content_type": content_type,



                        "bytes_read": bytes_read,



                    },



                )







        except urllib.error.HTTPError as e:



            return WebDocument(



                url=url_clean,



                status_code=e.code,



                error=f"HTTP {e.code}: {e.reason}",



                metadata={"provider": "http_fetch"},



            )



        except urllib.error.URLError as e:



            reason_str = str(e.reason)



            if "timed out" in reason_str.lower() or isinstance(e.reason, socket.timeout):



                return WebDocument(



                    url=url_clean,



                    status_code=504,



                    error=f"Fetch timed out after {effective_timeout}s.",



                    metadata={"provider": "http_fetch", "timeout": True},



                )



            return WebDocument(



                url=url_clean,



                status_code=502,



                error=f"Network error: {reason_str}",



                metadata={"provider": "http_fetch"},



            )



        except (TimeoutError, socket.timeout):



            return WebDocument(



                url=url_clean,



                status_code=504,



                error=f"Fetch timed out after {effective_timeout}s.",



                metadata={"provider": "http_fetch", "timeout": True},



            )



        except Exception as e:



            logger.warning("Unexpected error fetching '%s': %s", url_clean, e)



            return WebDocument(



                url=url_clean,



                status_code=500,



                error=f"Fetch error: {str(e)}",



                metadata={"provider": "http_fetch"},



            )

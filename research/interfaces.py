from abc import ABC, abstractmethod
from research.models import SearchResult, WebDocument


class SearchProvider(ABC):
    """Abstract interface for web search providers."""

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return self.__class__.__name__

    @abstractmethod
    def search(
        self,
        query: str,
        max_results: int = 5,
        timeout: float | None = None,
    ) -> SearchResult:
        """Execute a search query and return structured search results."""
        raise NotImplementedError


class FetchProvider(ABC):
    """Abstract interface for web document retrieval and content extraction."""

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return self.__class__.__name__

    @abstractmethod
    def fetch(
        self,
        url: str,
        timeout: float | None = None,
    ) -> WebDocument:
        """Fetch a web page and return extracted textual content."""
        raise NotImplementedError


class WebProvider(SearchProvider, FetchProvider, ABC):
    """Combined contract for providers supporting both search and fetch capabilities."""
    pass

"""Canonical source-type catalog for subscription recipes.

The subscription product is type-first: a source is classified into a
small, deterministic source type, and that type points at a versioned
recipe. Legacy coarse names (``rss``, ``youtube``, ``twitter``, etc.) are
kept as aliases so existing rows can keep working while new subscriptions
pin the canonical IDs in config.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceTypeSpec:
    id: str
    label: str
    icon: str
    recipe_id: str
    recipe_version: int
    execution_method: str
    capabilities: tuple[str, ...]
    default_interval_minutes: int
    aliases: tuple[str, ...] = ()
    auth_url: str = ""


SOURCE_TYPES: tuple[SourceTypeSpec, ...] = (
    SourceTypeSpec(
        id="rss.feed",
        label="RSS/Atom feed",
        icon="📡",
        recipe_id="rss.feed.default",
        recipe_version=1,
        execution_method="feed",
        capabilities=("fetch_url", "parse_rss", "parse_html", "extract_metadata"),
        default_interval_minutes=30,
        aliases=("rss",),
    ),
    SourceTypeSpec(
        id="youtube.channel",
        label="YouTube channel",
        icon="🎥",
        recipe_id="youtube.channel.default",
        recipe_version=1,
        execution_method="feed",
        capabilities=("fetch_url", "parse_html", "parse_rss", "extract_metadata"),
        default_interval_minutes=60,
        aliases=("youtube",),
        auth_url="https://accounts.google.com/ServiceLogin?service=youtube",
    ),
    SourceTypeSpec(
        id="github.releases",
        label="GitHub releases",
        icon="GH",
        recipe_id="github.releases.default",
        recipe_version=1,
        execution_method="feed",
        capabilities=("fetch_url", "parse_rss", "parse_html", "extract_metadata"),
        default_interval_minutes=240,
        aliases=("github", "github.releases"),
    ),
    SourceTypeSpec(
        id="x.profile",
        label="X profile",
        icon="𝕏",
        recipe_id="x.profile.default",
        recipe_version=1,
        execution_method="browser",
        capabilities=("chromux_navigate", "chromux_extract", "extract_metadata"),
        default_interval_minutes=60,
        aliases=("twitter", "x"),
        auth_url="https://twitter.com/login",
    ),
    SourceTypeSpec(
        id="linkedin.profile",
        label="LinkedIn profile",
        icon="💼",
        recipe_id="linkedin.profile.default",
        recipe_version=1,
        execution_method="browser",
        capabilities=("chromux_navigate", "chromux_extract", "extract_metadata"),
        default_interval_minutes=1440,
        aliases=("linkedin",),
        auth_url="https://www.linkedin.com/login",
    ),
    SourceTypeSpec(
        id="threads.profile",
        label="Threads profile",
        icon="@",
        recipe_id="threads.profile.default",
        recipe_version=1,
        execution_method="browser",
        capabilities=("chromux_navigate", "chromux_extract", "extract_metadata"),
        default_interval_minutes=1440,
        aliases=("threads",),
        auth_url="https://www.threads.net/login",
    ),
    SourceTypeSpec(
        id="substack.publication",
        label="Substack publication",
        icon="✉",
        recipe_id="substack.publication.default",
        recipe_version=1,
        execution_method="feed",
        capabilities=("fetch_url", "parse_rss", "parse_html", "extract_metadata"),
        default_interval_minutes=240,
        aliases=("substack",),
        auth_url="https://substack.com/sign-in",
    ),
    SourceTypeSpec(
        id="substack.tag",
        label="Substack tag",
        icon="✉",
        recipe_id="substack.tag.default",
        recipe_version=1,
        execution_method="api",
        capabilities=("fetch_url", "parse_json", "parse_html", "extract_metadata"),
        default_interval_minutes=240,
        aliases=(),
        auth_url="https://substack.com/sign-in",
    ),
    SourceTypeSpec(
        id="medium.publication",
        label="Medium publication",
        icon="M",
        recipe_id="medium.publication.default",
        recipe_version=1,
        execution_method="feed",
        capabilities=("fetch_url", "parse_rss", "parse_html", "extract_metadata"),
        default_interval_minutes=240,
        aliases=("medium",),
        auth_url="https://medium.com/m/signin",
    ),
    SourceTypeSpec(
        id="reddit.subreddit",
        label="Reddit subreddit",
        icon="R",
        recipe_id="reddit.subreddit.default",
        recipe_version=1,
        execution_method="api",
        capabilities=("fetch_url", "parse_json", "extract_metadata"),
        default_interval_minutes=120,
        aliases=("reddit",),
        auth_url="https://www.reddit.com/login",
    ),
    SourceTypeSpec(
        id="webpage",
        label="Generic webpage",
        icon="🌐",
        recipe_id="webpage.generic.default",
        recipe_version=1,
        execution_method="browser",
        capabilities=("chromux_navigate", "chromux_extract", "extract_metadata"),
        default_interval_minutes=1440,
        aliases=("agent",),
    ),
)


_BY_ID = {spec.id: spec for spec in SOURCE_TYPES}
_ALIASES: dict[str, str] = {}
for _spec in SOURCE_TYPES:
    _ALIASES[_spec.id] = _spec.id
    for _alias in _spec.aliases:
        _ALIASES[_alias] = _spec.id


def canonical_source_type(value: str | None) -> str:
    """Return the canonical source-type ID for a legacy or canonical value."""
    key = (value or "").strip().lower()
    return _ALIASES.get(key, key or "webpage")


def get_source_type_spec(value: str | None) -> SourceTypeSpec | None:
    """Look up a source-type spec by canonical ID or legacy alias."""
    return _BY_ID.get(canonical_source_type(value))


def is_supported_source_type(value: str | None) -> bool:
    return get_source_type_spec(value) is not None


def source_type_options() -> list[dict[str, str]]:
    """Return stable option payloads for the web UI."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "icon": spec.icon,
            "execution_method": spec.execution_method,
        }
        for spec in SOURCE_TYPES
    ]


def schedule_defaults() -> dict[str, int]:
    """Built-in schedule defaults for canonical IDs and legacy aliases."""
    defaults: dict[str, int] = {}
    for spec in SOURCE_TYPES:
        defaults[spec.id] = spec.default_interval_minutes
        for alias in spec.aliases:
            defaults[alias] = spec.default_interval_minutes
    return defaults


def auth_signin_homepages() -> dict[str, str]:
    """Sign-in URLs keyed by canonical IDs and legacy aliases."""
    homes: dict[str, str] = {}
    for spec in SOURCE_TYPES:
        if not spec.auth_url:
            continue
        homes[spec.id] = spec.auth_url
        for alias in spec.aliases:
            homes[alias] = spec.auth_url
    return homes


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return ""


def _path_parts(url: str) -> list[str]:
    try:
        return [part for part in urlparse(url).path.split("/") if part]
    except Exception:
        return []


def detect_source_type(url: str) -> str:
    """Return the canonical source type for a URL."""
    host = _host(url)
    lower_url = (url or "").lower()
    path_parts = _path_parts(url)
    if not host:
        return "webpage"

    if "youtube.com" in host or host == "youtu.be":
        return "youtube.channel"
    if host == "github.com" and len(path_parts) >= 3:
        release_part = path_parts[2].lower()
        if release_part in {"releases", "releases.atom"}:
            return "github.releases"
    if host in {"twitter.com", "x.com"} or host.endswith(".twitter.com"):
        return "x.profile"
    if "nitter" in host:
        return "x.profile"
    if host.endswith("linkedin.com") or "linkedin.com" in host:
        return "linkedin.profile"
    if host.endswith("threads.net"):
        return "threads.profile"
    if host.endswith("substack.com"):
        if len(path_parts) >= 2 and path_parts[0].lower() == "t":
            return "substack.tag"
        return "substack.publication"
    if host in {"a16z.news"} and len(path_parts) >= 2 and path_parts[0].lower() == "t":
        return "substack.tag"
    if host.endswith("medium.com"):
        return "medium.publication"
    if host.endswith("reddit.com") or host == "redd.it":
        return "reddit.subreddit"
    if lower_url.rstrip("/").endswith((".xml", ".rss", ".atom")) or "/feed" in lower_url:
        return "rss.feed"
    return "webpage"


def classify_url(url: str, source_type: str | None = None) -> dict[str, object]:
    """Classify a URL and include the default recipe pin metadata."""
    selected = canonical_source_type(source_type) if source_type else detect_source_type(url)
    spec = get_source_type_spec(selected) or get_source_type_spec("webpage")
    assert spec is not None
    host = _host(url)
    return {
        "source_type": spec.id,
        "recipe_base": spec.id,
        "recipe_id": spec.recipe_id,
        "recipe_version": spec.recipe_version,
        "recipe_channel": "stable",
        "execution_method": spec.execution_method,
        "capabilities": list(spec.capabilities),
        "suggested_title": host or url,
        "icon": spec.icon,
        "label": spec.label,
        "has_rss_hint": spec.execution_method == "feed",
    }


def default_recipe_config(source_type: str) -> dict[str, object]:
    """Config fields written when a subscription is created or normalized."""
    spec = get_source_type_spec(source_type)
    if spec is None:
        return {}
    return {
        "recipe_base": spec.id,
        "recipe_id": spec.recipe_id,
        "recipe_version": spec.recipe_version,
        "recipe_channel": "stable",
        "fetch_method": spec.execution_method,
        "recipe_capabilities": list(spec.capabilities),
    }


__all__ = [
    "SourceTypeSpec",
    "SOURCE_TYPES",
    "auth_signin_homepages",
    "canonical_source_type",
    "classify_url",
    "default_recipe_config",
    "detect_source_type",
    "get_source_type_spec",
    "is_supported_source_type",
    "schedule_defaults",
    "source_type_options",
]

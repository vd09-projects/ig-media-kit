"""ig_media_kit — anonymous Instagram reel fetch engine + flat-file store.

Exposes the shared fetch primitive (:mod:`ig_media_kit.fetch`), the CSV+YAML
store (:mod:`ig_media_kit.store`), the anonymous HTTP client
(:mod:`ig_media_kit.http_client`), the config loader
(:mod:`ig_media_kit.config`), and a FastMCP server skeleton
(:mod:`ig_media_kit.mcp_server`).

ANONYMOUS ONLY: no login, no session cookies, no account — ever. See CLAUDE.md.
"""

__version__ = "0.1.0"

# The IG public web app id — mandatory header on every metadata API call.
IG_APP_ID = "936619743392459"

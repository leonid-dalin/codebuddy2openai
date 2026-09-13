"""Backend protocol contract.

Values read off the WorkBuddy desktop client. Check this file first when
a client update breaks requests
"""

from __future__ import annotations

# Realm backends, selected by the auth file's domain field.
BACKEND_CN = "https://copilot.tencent.com"
BACKEND_DEFAULT_DOMAIN = "www.codebuddy.cn"
BACKEND_BY_DOMAIN = {
    "www.workbuddy.ai": "https://www.workbuddy.ai",
    "www.codebuddy.cn": BACKEND_CN,
}

CHAT_COMPLETIONS_PATH = "/v2/chat/completions"
TOKEN_REFRESH_PATH = "/v2/plugin/auth/token/refresh"

# Retry over the desktop-token path when upstream answers with these codes.
TOKEN_PATH_RETRY_CODES = frozenset({6004, 11128})

# Refresh a token this long before expiry.
EXPIRY_MARGIN_MS = 60_000

HEADER_ACCESS_TOKEN = "Authorization"
HEADER_USER_ID = "X-User-Id"
HEADER_ENTERPRISE_ID = "X-Enterprise-Id"
HEADER_TENANT_ID = "X-Tenant-Id"
HEADER_DOMAIN = "X-Domain"
HEADER_REFRESH_TOKEN = "X-Refresh-Token"
HEADER_REFRESH_SOURCE = "X-Auth-Refresh-Source"
REFRESH_SOURCE_VALUE = "plugin"

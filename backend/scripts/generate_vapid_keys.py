"""Print a new VAPID key pair for browser push.

    python scripts/generate_vapid_keys.py

Put VAPID_PRIVATE_KEY in backend/.env with WEBPUSH_ENABLED=true. The public key
is derived from it and served to browsers by GET /api/v1/push/public-key, so it
does not need to be configured anywhere. Changing the private key invalidates
every existing subscription: devices have to switch push on again.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.delivery import webpush

private = webpush.generate_private_key()
print(f"VAPID_PRIVATE_KEY={private}")
print(f"# public key: {webpush.application_server_key(webpush.load_private_key(private))}")

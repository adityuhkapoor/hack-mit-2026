"""Post a card to the camera's shared Instagram account.

Uses the Instagram API with Instagram Login (graph.instagram.com): a professional (business or creator)
Instagram account and one access token from the Meta app dashboard, no Facebook page needed. The account
id is looked up from the token. Instagram fetches the image itself, so it must be at a public URL (the box).

    uv run python -m nimbus_cam.instagram          # check the token: prints the account it posts as
"""

from __future__ import annotations

import sys
import time

import httpx

from . import secrets

GRAPH = "https://graph.instagram.com/v21.0"


class NotConfigured(RuntimeError):
    pass


def _token() -> str:
    token = secrets.get("ig_token")
    if not token:
        raise NotConfigured("Instagram is not set up on this camera (Keychain ig-token)")
    return token


def account(c: httpx.Client, token: str) -> dict:
    r = c.get(f"{GRAPH}/me", params={"fields": "user_id,username,account_type", "access_token": token})
    r.raise_for_status()
    return r.json()


def post(image_url: str, caption: str) -> str:
    token = _token()
    with httpx.Client(timeout=60) as c:
        user = secrets.get("ig_user") or account(c, token)["user_id"]
        media = c.post(f"{GRAPH}/{user}/media", params={"image_url": image_url, "caption": caption,
                                                        "access_token": token})
        media.raise_for_status()
        creation = media.json()["id"]
        # The container is processed asynchronously; publishing too early fails with "not ready".
        for _ in range(15):
            st = c.get(f"{GRAPH}/{creation}", params={"fields": "status_code", "access_token": token}).json()
            if st.get("status_code") in ("FINISHED", None):
                break
            if st.get("status_code") == "ERROR":
                raise RuntimeError("Instagram could not process the image")
            time.sleep(1.5)
        pub = c.post(f"{GRAPH}/{user}/media_publish", params={"creation_id": creation, "access_token": token})
        pub.raise_for_status()
        return pub.json()["id"]


if __name__ == "__main__":
    try:
        with httpx.Client(timeout=20) as c:
            a = account(c, _token())
    except NotConfigured as e:
        sys.exit(str(e))
    except httpx.HTTPStatusError as e:
        sys.exit(f"Instagram rejected the token: {e.response.text[:300]}")
    print(f"posting as @{a.get('username')} ({a.get('account_type')}), id {a.get('user_id')}")

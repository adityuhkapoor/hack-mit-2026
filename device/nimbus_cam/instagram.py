"""Post a card to the camera's shared Instagram account (Graph API content publishing).

Needs an Instagram business or creator account linked to a Facebook page, and a long-lived token with
instagram_content_publish. Instagram fetches the image itself, so it must be at a public URL (the box).
"""

from __future__ import annotations

import time

import httpx

from . import secrets

GRAPH = "https://graph.facebook.com/v21.0"


class NotConfigured(RuntimeError):
    pass


def post(image_url: str, caption: str) -> str:
    user, token = secrets.get("ig_user"), secrets.get("ig_token")
    if not (user and token):
        raise NotConfigured("Instagram is not set up on this camera (ig-user-id / ig-token)")
    with httpx.Client(timeout=60) as c:
        media = c.post(f"{GRAPH}/{user}/media", params={"image_url": image_url, "caption": caption,
                                                        "access_token": token})
        media.raise_for_status()
        creation = media.json()["id"]
        # The container is processed asynchronously; publishing too early fails with "not ready".
        for _ in range(10):
            st = c.get(f"{GRAPH}/{creation}", params={"fields": "status_code", "access_token": token}).json()
            if st.get("status_code") in ("FINISHED", None):
                break
            if st.get("status_code") == "ERROR":
                raise RuntimeError("Instagram could not process the image")
            time.sleep(1.5)
        pub = c.post(f"{GRAPH}/{user}/media_publish", params={"creation_id": creation, "access_token": token})
        pub.raise_for_status()
        return pub.json()["id"]

from typing import Annotated

from fastapi import Header, HTTPException

# Trusted-local demonstration personas. Headers are intentionally selectable in the UI.
ROLES = {"analyst": "analyst", "admin": "admin", "viewer": "viewer"}


def actor(x_demo_user: Annotated[str | None, Header()] = None) -> str:
    if x_demo_user not in ROLES:
        raise HTTPException(401, "Select a valid local demo user.")
    return x_demo_user


def require(actor_name, *roles):
    if ROLES[actor_name] not in roles:
        raise HTTPException(403, "This demo role cannot perform the requested operation.")


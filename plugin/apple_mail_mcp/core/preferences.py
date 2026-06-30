"""The @inject_preferences docstring decorator and its type variables."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

from apple_mail_mcp.server import USER_PREFERENCES

P = ParamSpec("P")
R = TypeVar("R")


def inject_preferences(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that appends user preferences to tool docstrings"""
    if USER_PREFERENCES:
        if func.__doc__:
            func.__doc__ = func.__doc__.rstrip() + f"\n\nUser Preferences: {USER_PREFERENCES}"
        else:
            func.__doc__ = f"User Preferences: {USER_PREFERENCES}"
    return func

"""Opt-in ctypes adapter for the native SDL2 frame presenter prototype."""

from .presenter import NativePresenter, PresenterError, map_window_to_logical

__all__ = ["NativePresenter", "PresenterError", "map_window_to_logical"]

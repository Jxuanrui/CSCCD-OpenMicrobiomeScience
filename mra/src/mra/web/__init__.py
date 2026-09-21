"""Local FastAPI workbench for inspecting the MRA policy and audit ledger."""

from mra.web.app import create_app

__all__ = ["create_app"]

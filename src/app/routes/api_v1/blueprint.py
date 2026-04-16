"""Singleton Flask blueprint for `/api/v1`."""

from flask import Blueprint

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

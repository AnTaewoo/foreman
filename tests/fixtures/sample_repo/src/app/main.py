"""App factory. Flask is optional at import time so the fixture works without it installed."""

from __future__ import annotations

from app.models import UserStore

store = UserStore()


def greet(name: str) -> str:
    return f"hello, {name}"


def create_app() -> object:
    try:
        from flask import Flask, jsonify
    except ImportError:  # 픽스처 테스트 환경에는 flask가 없을 수 있다
        return {"routes": ["/users"], "store": store}
    app = Flask(__name__)

    @app.get("/users")
    def list_users() -> object:
        return jsonify([u.__dict__ for u in store.all()])

    return app

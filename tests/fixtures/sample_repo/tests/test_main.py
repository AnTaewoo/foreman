from app.main import greet
from app.models import UserStore


def test_greet() -> None:
    assert greet("bob") == "hello, bob"


def test_store() -> None:
    s = UserStore()
    u = s.add("ann")
    assert s.get(u.id) == u and len(s.all()) == 1

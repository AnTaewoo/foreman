"""In-memory models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class User:
    id: int
    name: str


@dataclass
class UserStore:
    _users: dict[int, User] = field(default_factory=dict)
    _next_id: int = 1

    def add(self, name: str) -> User:
        user = User(id=self._next_id, name=name)
        self._users[user.id] = user
        self._next_id += 1
        return user

    def get(self, user_id: int) -> User | None:
        return self._users.get(user_id)

    def all(self) -> list[User]:
        return list(self._users.values())

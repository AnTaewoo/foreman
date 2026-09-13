"""전역 픽스처. LLM은 항상 FakeProvider (실 호출은 PC-3/PC-5 스크립트에서만)."""

from __future__ import annotations

import pytest

from agents.llm.fake import FakeProvider


@pytest.fixture
def fake_provider() -> FakeProvider:
    """빈 스크립트. 테스트가 ``fake_provider.script.extend([...])``로 채운다."""
    return FakeProvider(script=[])

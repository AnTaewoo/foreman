# Coding Agent 워커 (설계 §10.3): Task당 컨테이너 1개, git worktree, secrets 없음.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# 대상 repo의 pytest/ruff를 돌려야 하므로 dev 그룹까지 설치한다
RUN uv sync --frozen --all-groups --no-install-project
COPY control_plane ./control_plane
COPY agents ./agents
COPY github_adapter ./github_adapter
COPY worker ./worker
RUN uv sync --frozen --all-groups

RUN useradd -m -u 10001 worker && mkdir -p /work && chown -R worker:worker /app /work
USER worker
# 볼륨으로 마운트된 remote/worktree는 소유자가 달라도 허용
RUN git config --global --add safe.directory '*' \
    && git config --global user.name foreman-agent \
    && git config --global user.email agent@foreman.local
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 HOME=/home/worker

ENTRYPOINT ["python", "-m", "worker"]

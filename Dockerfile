FROM ghcr.io/astral-sh/uv:python3.13-trixie

COPY ./ghostroot/pyproject.toml /ghostroot/pyproject.toml
COPY ./ghostroot/uv.lock /ghostroot/uv.lock
WORKDIR /ghostroot
RUN uv sync --frozen --no-install-project -i https://mirrors.aliyun.com/pypi/simple/

COPY ./ghostroot /ghostroot
RUN uv sync --frozen -i https://mirrors.aliyun.com/pypi/simple/

ENV TZ=Asia/Shanghai
FROM python:3.12-slim-bookworm AS base-builder

RUN apt-get update --fix-missing && apt-get install -y \
    software-properties-common \
    net-tools \
    curl \
    fontconfig \
    procps \
    supervisor \
    vim \
    libmagic1 \
    libmagic-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libreoffice \
    ffmpeg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

FROM base-builder AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_VIRTUALENVS_CREATE=1 \
    UV_VIRTUALENVS_IN_PROJECT=1 \
    UV_NO_INTERACTION=1 \
    UV_PYTHON=/usr/local/bin/python \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY . /app

WORKDIR /app

RUN mkdir -p /models/paddleocr_model && \
    unzip -q -o /app/paddleocr_model.zip -d /models/paddleocr_model

RUN echo "Checking files in /app:" && ls -al /app

# RUN mv pyproject.prod.toml pyproject.toml  # 파일이 존재하지 않음

# Initialize virtual environment and install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

RUN docling-tools models download -o /models

RUN --mount=type=cache,target=/root/.cache/huggingface \
    huggingface-cli download mncai/doc_parser_models --local-dir /models/doc_parser_models

RUN --mount=type=cache,target=/root/.cache/huggingface \
    huggingface-cli download mncai/doc_parser_models --include "sentence-transformers-all-MiniLM-L6-v2/*" --local-dir /models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2

RUN --mount=type=cache,target=/root/.cache/nltk \
    python -c "import nltk; nltk.download('all')"

WORKDIR /app/ocr_grpc_server

RUN python -m venv .venv \
 && ./.venv/bin/python -m pip install --upgrade pip \
 && ./.venv/bin/pip install grpcio grpcio-tools paddleocr pillow numpy \
 && ./.venv/bin/pip install paddlepaddle

FROM base-builder AS runtime

COPY . /app

ENV DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
COPY --from=builder /models /models
COPY --from=builder /root/nltk_data /root/nltk_data
COPY --from=builder /app/ocr_grpc_server/.venv /app/ocr_grpc_server/.venv

WORKDIR /app

# RUN mv pyproject.prod.toml pyproject.toml  # 파일이 존재하지 않음

RUN mkdir -p /usr/share/fonts && \
    tar zxvf /app/HCRBatang.ttf.tar.gz -C /usr/share/fonts/ && \
    fc-cache -f -v && \
    ln -s /app/configs/supervisor.conf /etc/supervisor/conf.d/supervisor.conf && \
    mkdir /usr/share/tessdata/ && \
    tar zxvf /app/tessedata.tar.gz -C /usr/share/tessdata/

ENV DOCLING_ARTIFACTS_PATH=/models

RUN mv /models/paddleocr_model/paddleocr_model/* /models/paddleocr_model/

CMD ["supervisord", "-n"]
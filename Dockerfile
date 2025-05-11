FROM python:3.9-alpine

RUN apk update && apk add --no-cache \
    ffmpeg \
    bash \
    libmagic \
    build-base \
    libffi-dev \
    libssl1.1 \
    && rm -rf /var/cache/apk/*

WORKDIR /app

COPY . /app

RUN python -m venv .venv

RUN .venv/bin/pip install --upgrade pip
RUN .venv/bin/pip install -r requirements.txt

CMD [".venv/bin/python", "main.py"]

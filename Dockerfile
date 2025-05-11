FROM alpine:3.21

RUN apk update && apk add --no-cache \
    python3 \
    python3-dev \
    py3-pip \
    bash \
    ffmpeg \
    libmagic \
    build-base \
    && rm -rf /var/cache/apk/*

RUN ln -sf python3 /usr/bin/python

WORKDIR /app

COPY . /app

RUN python -m venv .venv

RUN .venv/bin/pip install --upgrade pip
RUN .venv/bin/pip install -r requirements.txt

CMD [".venv/bin/python", "main.py"]

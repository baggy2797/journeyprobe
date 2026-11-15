# Browsers and system deps come from the official Playwright image; keep the tag in sync with the pin below.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install "playwright==1.64.1" .
COPY examples ./examples

USER pwuser
WORKDIR /work
ENTRYPOINT ["journeyprobe"]
CMD ["--help"]

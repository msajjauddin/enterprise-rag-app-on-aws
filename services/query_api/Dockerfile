FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /srv

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

RUN useradd --system --uid 10001 appuser
USER appuser

EXPOSE 8000

# Keep-alive must outlive the ALB idle timeout (300 s) to avoid 502s on reused connections
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --timeout-keep-alive 310 --proxy-headers --forwarded-allow-ips='*' --no-access-log"]

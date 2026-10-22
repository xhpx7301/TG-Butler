FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    TG_BUTLER_CONTROL_MODE=supervisor

WORKDIR /opt/tg-butler

RUN python -m venv /opt/venv

COPY pyproject.toml README.md .env.example config.example.yaml ./
COPY src ./src

RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir . supervisor \
    && useradd --system --home-dir /var/lib/tg-butler --shell /usr/sbin/nologin tgbutler \
    && mkdir -p /var/lib/tg-butler/data \
    && chown -R tgbutler:tgbutler /var/lib/tg-butler

COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/entrypoint.sh /usr/local/bin/tg-butler-entrypoint
COPY docker/tg-butler-control /usr/local/bin/tg-butler-control

RUN chmod 755 /usr/local/bin/tg-butler-entrypoint /usr/local/bin/tg-butler-control \
    && chmod 755 /etc/supervisor \
    && chmod 644 /etc/supervisor/supervisord.conf

EXPOSE 15678

ENTRYPOINT ["/usr/local/bin/tg-butler-entrypoint"]

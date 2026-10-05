FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CABLE_DB=/app/data/cable.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY templates ./templates
COPY wsgi.py .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5000

# 启动时由应用工厂自动建表（不存在才创建）
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "60", "wsgi:app"]

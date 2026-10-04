FROM python:3.13.5-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tablekeeper ./tablekeeper
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 50 --bind 0.0.0.0:${PORT:-8080} --timeout 30 'tablekeeper.app:create_app()'"]

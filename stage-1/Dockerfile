FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY codec.py rules.py domain.py stateio.py server.py ./
RUN python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin'); ZoneInfo('America/New_York')"
ENV PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]

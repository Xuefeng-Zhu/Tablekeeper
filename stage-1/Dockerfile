FROM python:3.12.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY tablekeeper /app/tablekeeper
RUN python -c "import sqlite3,hashlib; from zoneinfo import ZoneInfo; sqlite3.connect(':memory:').execute('select 1'); hashlib.scrypt(b'probe',salt=b'0123456789abcdef',n=16384,r=8,p=1,dklen=64,maxmem=67108864); ZoneInfo('Europe/Berlin'); ZoneInfo('America/New_York')"
EXPOSE 8080
CMD ["python", "-m", "tablekeeper.server"]

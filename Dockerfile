FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/downloads

ENV DOWNLOAD_ROOT=/data/downloads
ENV FILE_TTL_SECONDS=3600
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "app.py"]

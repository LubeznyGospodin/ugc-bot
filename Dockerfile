# Явная сборка бота с ffmpeg (nixpacks/railpack aptPkgs не ставили его — см. лог ffmpeg=None).
# ffmpeg/ffprobe нужны для нормализации работ-видео (обложка+размеры, docs/CHANNEL_POSTING.md).
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "main.py"]

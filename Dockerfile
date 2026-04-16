FROM python:3.11-slim

# Install ffmpeg (required for voice message transcription)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --no-build-isolation openai-whisper==20240930 && \
    pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python3", "bot.py"]

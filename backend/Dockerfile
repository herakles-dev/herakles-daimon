FROM python:3.12-slim
WORKDIR /app

# ffmpeg is required by yt-dlp to convert subtitle formats (VTT/SRT)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libchromaprint-tools libsndfile1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11-slim

WORKDIR /app

# Minimal OS libs for OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Use lightweight cloud requirements (OpenCV YuNet + SFace uses ~110MB RAM, fits 512MB Render free tier)
COPY backend/requirements-cloud.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# Pre-download YuNet (~230KB) and SFace (~37MB) ONNX models at build time for instant cold starts
RUN python -c "import urllib.request, pathlib; \
    m = pathlib.Path('/app/backend/models'); m.mkdir(exist_ok=True); \
    urllib.request.urlretrieve('https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx', str(m / 'face_detection_yunet_2023mar.onnx')); \
    urllib.request.urlretrieve('https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx', str(m / 'face_recognition_sface_2021dec.onnx')); \
    print('Models pre-cached successfully')"

WORKDIR /app/backend

ENV HOST=0.0.0.0
ENV PORT=8000
ENV FACE_BACKEND=opencv_sface

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

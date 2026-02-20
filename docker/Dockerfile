FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libevdev2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY macropad_bridge.py .

CMD ["python", "macropad_bridge.py"]

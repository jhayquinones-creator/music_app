FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=host.docker.internal:0.0 \
    BLAISE_DATA_DIR=/data \
    QT_X11_NO_MITSHM=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libvlc-dev \
        python3 \
        python3-pip \
        python3-tk \
        python3-venv \
        vlc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY BLAISE.py .
RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "BLAISE.py"]

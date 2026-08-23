FROM python:3.12-slim
ARG XRAY_VERSION=26.3.27
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip" -o /tmp/xray.zip \
 && unzip -p /tmp/xray.zip xray > /usr/local/bin/xray \
 && chmod 0755 /usr/local/bin/xray \
 && rm /tmp/xray.zip
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY frontend ./frontend
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1 XDG_CACHE_HOME=/tmp
EXPOSE 8000
CMD ["python","-m","app.main"]

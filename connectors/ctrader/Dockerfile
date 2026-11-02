FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock
COPY ctrader_connector.py .
RUN useradd --system --uid 10002 --home /app connector \
 && mkdir -p /data && chown connector /data && chmod 700 /data
USER connector
VOLUME ["/data"]
CMD ["python", "ctrader_connector.py"]

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY capital_connector.py .
RUN useradd --system --uid 10003 --home /app connector \
 && mkdir -p /data && chown connector /data && chmod 700 /data
USER connector
VOLUME ["/data"]
CMD ["python", "capital_connector.py"]

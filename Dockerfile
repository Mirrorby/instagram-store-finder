FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
COPY requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements-local.txt
COPY store_finder ./store_finder
COPY config ./config
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data IMPORT_PUBLISH_ENABLED=0
CMD ["python", "-m", "store_finder.railway_service"]

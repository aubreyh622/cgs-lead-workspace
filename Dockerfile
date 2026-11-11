# CGS Lead Workspace v2 as a container, for Google Cloud Run. See docs/DEPLOY_GOOGLE_CLOUD.md.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHON_DOTENV_DISABLED=1 \
    CGS_HOST=0.0.0.0

WORKDIR /app
COPY requirements.lock requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.lock -r requirements-cloud.txt

COPY . .
RUN useradd --create-home workspace && mkdir -p runtime && chown -R workspace runtime
USER workspace

# Cloud Run sets PORT. Settings come from environment variables and Secret Manager, never from a .env file in the image.
CMD ["python", "-m", "api.main"]

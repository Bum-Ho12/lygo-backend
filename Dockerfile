FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng libmagic1 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml .
COPY alembic.ini .
COPY app ./app
RUN pip install --no-cache-dir .
RUN useradd -m lygo
USER lygo
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

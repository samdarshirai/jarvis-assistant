FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd -m jarvis
USER jarvis
CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8000"]

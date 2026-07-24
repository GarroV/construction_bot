FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
COPY prompts/ prompts/
COPY locales/ locales/
COPY migrations/ migrations/
COPY scripts/ scripts/
RUN pip install --no-cache-dir .
CMD ["python", "-m", "src.main"]

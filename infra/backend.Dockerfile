FROM python:3.13.5-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt backend/build-requirements.txt ./
RUN pip install --no-cache-dir -r build-requirements.txt -c constraints.txt -r requirements.txt
COPY backend/ ./
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
RUN useradd --uid 10001 --create-home intent
USER intent
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "intent_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]

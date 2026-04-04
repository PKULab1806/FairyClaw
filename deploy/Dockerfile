FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY fairyclaw /app/fairyclaw
COPY main.py /app/main.py
COPY config /app/config

RUN pip install --no-cache-dir -e .

ENV FAIRYCLAW_HOST=0.0.0.0
ENV FAIRYCLAW_PORT=8000

EXPOSE 8000

CMD ["python", "main.py"]

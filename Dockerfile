FROM python:3.13-slim

WORKDIR /app

COPY server.py .
COPY index.html .
COPY pricing_tool.py .
COPY test_matrix.json .

RUN mkdir -p snapshots

EXPOSE 8080

CMD ["python3", "server.py"]

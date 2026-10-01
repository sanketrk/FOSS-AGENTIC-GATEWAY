FROM python:3.13-alpine
WORKDIR /demo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py prepare.py serve.py client.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1001:0
ENTRYPOINT ["python"]

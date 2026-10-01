FROM python:3.13-alpine
WORKDIR /demo
COPY sdk/python /sdk
RUN pip install --no-cache-dir /sdk
COPY examples/banking/infrastructure/banking_demo /demo/banking_demo
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1001:0
ENTRYPOINT ["python"]

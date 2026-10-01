FROM python:3.13-alpine
WORKDIR /demo
COPY sdk/python /sdk
RUN pip install --no-cache-dir /sdk
COPY examples/banking/infrastructure/banking_demo /demo/banking_demo
COPY examples/banking/transaction-review-agent/review_agent.py /demo/review_agent.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1001:0
ENTRYPOINT ["python", "review_agent.py"]

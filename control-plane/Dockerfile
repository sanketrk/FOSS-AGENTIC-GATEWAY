FROM python:3.13-alpine
WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CONTROL_PLANE_HOST=0.0.0.0 REGISTRY_DATABASE=/data/registry.sqlite3
RUN mkdir /data && chgrp 0 /data && chmod g+rwx /data
USER 1001
EXPOSE 8080
CMD ["python", "app.py"]

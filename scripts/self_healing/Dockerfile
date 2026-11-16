FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY self_healing.py .
USER 65532:65532
EXPOSE 8443
ENTRYPOINT ["python", "-u", "self_healing.py"]

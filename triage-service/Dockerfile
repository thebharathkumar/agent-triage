FROM python:3.12-slim

WORKDIR /app

# Install the triage package and service extras from the parent directory
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the triage source package
COPY triage_src/ ./src/triage/

# Copy the service files
COPY main.py ui.html ./

ENV TRIAGE_RUNS_DIR=/data/runs
ENV TRIAGE_SPANS_DIR=/data/spans
ENV TRIAGE_REFRESH_SECONDS=60
ENV TRIAGE_TOP_N=3

EXPOSE 7070

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7070"]

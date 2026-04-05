FROM python:3.13-slim

WORKDIR /app

COPY aria_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY aria_service/ ./aria_service/

EXPOSE 8000

CMD ["python", "-m", "aria_service.main"]

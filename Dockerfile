FROM python:3.10
WORKDIR /app
COPY . /app
RUN pip install --upgrade pip
RUN pip install aiogram==2.25.1 aiohttp
CMD ["python", "main.py"]

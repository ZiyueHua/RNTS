FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目
COPY . .

# 数据目录
RUN mkdir -p data
VOLUME /app/data

# 配置目录（持久化）
VOLUME /app/config

EXPOSE 8000

# 单 worker，确保 APScheduler 不重复执行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

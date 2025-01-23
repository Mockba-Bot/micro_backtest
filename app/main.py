import os
import sys
from fastapi import FastAPI
from app.routes import backtest_router, status_router

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(
    title="Crypto Backtest API",
    version="1.0.0",
    description="API for running cryptocurrency backtests"
)

# Include the backtest routes
app.include_router(backtest_router, prefix="/api/v1/backtest")
app.include_router(status_router, prefix="/api/v1/backtest")

# run update of tables
# alembic revision --autogenerate -m "initial tables"
# commit
# alembic upgrade head
# Run project
# uvicorn app.main:app --reload
# redis-cli flushdb
# redis-cli flushall
# celery -A app.tasks.celery_app.celery_app worker --loglevel=info --concurrency=8

web: uvicorn src.api.main:app --host 0.0.0.0 --port $PORT
worker: python -m rq worker codeguard --url $REDIS_URL
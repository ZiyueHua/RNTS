#!/bin/bash
cd /workspace/rnts
exec python3.12 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

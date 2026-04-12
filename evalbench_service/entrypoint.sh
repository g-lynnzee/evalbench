#!/bin/bash
# evalbench_service/entrypoint.sh

if [[ "$CLOUD_RUN" == "True" ]]; then
    echo "Cloud Run detected. Starting gunicorn frontend and background precompute..."
    
    # Start background precomputation loop
    python /evalbench/viewer/run_precompute.py &
    
    # Ensure we are in the viewer directory for gunicorn to find main:me
    cd /evalbench/viewer
    exec gunicorn -w 12 -k gevent main:me --bind :${PORT:-3000} --forwarded-allow-ips="*" --timeout 120
else
    echo "Starting supervisord to manage multiple processes..."
    exec /usr/bin/supervisord -c /evalbench/supervisord.conf
fi

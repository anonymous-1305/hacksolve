FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 DATA_DIR=/data \
    APP_ENV=production COOKIE_SECURE=1 TRUST_PROXY=1
WORKDIR /app
RUN useradd -m -u 10001 app && mkdir /data && chown app /data
COPY --chown=app . /app
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')" || exit 1
CMD ["python", "run.py", "--no-browser"]

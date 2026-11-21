# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image we labeled every commit in: its Python and SQLite
# decide which sqlglot tests run, so keep it if your image runs them.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# Build start time: build/impact.py runs its optional passes only while the build
# is well inside the 10-minute limit (a stale stamp from a cached layer is ignored).
RUN date +%s > /opt/br_build_start

# sqlglot at the pinned commit (allowed source), unpacked to /opt/sqlglot.
ARG SQLGLOT_SHA=eb11aec4be8cb2122afe713a4816662c3f0fb2fa
RUN python -c "import io,tarfile,urllib.request,os; \
d=urllib.request.urlopen('https://github.com/tobymao/sqlglot/archive/${SQLGLOT_SHA}.tar.gz').read(); \
tarfile.open(fileobj=io.BytesIO(d)).extractall('/opt', filter='data'); \
os.rename('/opt/sqlglot-${SQLGLOT_SHA}', '/opt/sqlglot')"

# The labeling environment's exact packages, for build steps that run sqlglot's tests.
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock

COPY . /app
# Each layer's build/<layer>.py writes maps to /app/maps/<layer>; nobody must read them.
RUN python /app/build/run_all.py --sqlglot /opt/sqlglot --out /app/maps \
 && chmod -R a+rX /app /opt/sqlglot

CMD ["python", "/app/selector.py"]

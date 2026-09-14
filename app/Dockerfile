FROM python:3.11-slim

# WHICH BUILD THIS IS, baked in rather than configured.
#
# It has to come from the build, because the whole point is that it cannot be
# set wrong. A value in .env would be per-node configuration -- exactly the
# thing that drifts -- so a node could claim to be running a build it is not,
# which is worse than saying nothing.
#
# "unknown" is the honest default for an image built without it, and
# check-fleet.sh reports that rather than treating it as agreement.
ARG CYCLONE_BUILD=unknown
ENV CYCLONE_BUILD=$CYCLONE_BUILD

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

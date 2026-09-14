@echo off
REM ONE variable drives both the image tag and the build stamped inside it.
REM Two literals would eventually disagree, and the failure is an image that
REM reports a version it is not -- worse than reporting nothing, because
REM check-fleet.sh would then confirm agreement that does not exist.
set CYCLONE_BUILD=1.0.4

docker compose build
docker push ghcr.io/tjdaley/jdbot-cyclone-api:%CYCLONE_BUILD%
docker push ghcr.io/tjdaley/jdbot-cyclone-frontend:%CYCLONE_BUILD%
docker image prune --force

echo.
echo Built and pushed %CYCLONE_BUILD%.
echo Update the image: tags in docker-compose.yml on each node, then:
echo     docker compose -f docker-compose.yml up -d
echo     ./check-fleet.sh https://cyclone.jdbot.us
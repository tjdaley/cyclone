@echo off
setlocal

REM ONE variable drives both the image tag and the build stamped inside it.
REM Two literals would eventually disagree, and the failure is an image that
REM reports a version it is not -- worse than reporting nothing, because
REM check-fleet.sh would then confirm agreement that does not exist.
set CYCLONE_BUILD=1.0.4

REM ── PREFLIGHT: the frontend's build args come from THIS machine's .env ──────
REM
REM docker-compose.yml interpolates ${SUPABASE_URL} and ${SUPABASE_ANON_KEY}
REM into the frontend build on the HOST, before any container exists. Compose
REM substitutes an EMPTY STRING for a name it cannot find, prints one "variable
REM is not set" line, and exits 0. The image builds. The container is healthy.
REM nginx serves index.html. Nothing appears in any server log.
REM
REM And every browser gets a BLANK WHITE PAGE, because createClient("") throws
REM "supabaseUrl is required." at module scope before React mounts.
REM
REM That shipped. SUPABASE_URL was removed from .env during the SSM migration --
REM correctly, as far as the API was concerned, since it reads the value at
REM runtime and stayed healthy throughout. The frontend cannot: its copy is
REM COMPILED IN, so the build machine must hold these two whatever the servers
REM do. Refusing to build is the only moment anything can say so.
if not exist ".env" (
    echo.
    echo ERROR: no .env in this directory.
    echo.
    echo Compose reads the frontend's build args from it. Copy .env.example
    echo and fill it in before building.
    exit /b 1
)

for %%V in (SUPABASE_URL SUPABASE_ANON_KEY) do (
    findstr /R /B /C:"%%V=..*" .env >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: %%V is missing or empty in .env on THIS machine.
        echo.
        echo It is compiled into the frontend bundle as a Vite build arg, so it
        echo has to be here at BUILD time. Without it compose substitutes an
        echo empty string, the build succeeds, and the deployed app is a blank
        echo white page on every route with nothing in any server log.
        echo.
        echo This is unrelated to whether the SERVERS hold it: the API reads
        echo SUPABASE_URL at runtime and can get it from SSM. The browser
        echo bundle cannot.
        echo.
        echo Put it back in .env and run this again.
        exit /b 1
    )
)

echo Preflight OK -- frontend build args are present.
echo.

docker compose build
if errorlevel 1 exit /b 1

docker push ghcr.io/tjdaley/jdbot-cyclone-api:%CYCLONE_BUILD%
if errorlevel 1 exit /b 1
docker push ghcr.io/tjdaley/jdbot-cyclone-frontend:%CYCLONE_BUILD%
if errorlevel 1 exit /b 1

docker image prune --force

echo.
echo Built and pushed %CYCLONE_BUILD%.
echo Update the image: tags in docker-compose.yml on each node, then:
echo     docker compose -f docker-compose.yml up -d
echo     ./check-fleet.sh https://cyclone.jdbot.us
echo.
echo Then LOAD THE APP IN A BROWSER before calling it done. A frontend built
echo without its build args is healthy from every angle the server can see.

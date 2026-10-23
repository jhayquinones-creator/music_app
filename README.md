# BLAISE

BLAISE is a CustomTkinter desktop app. On Windows with Docker Desktop, its Linux GUI is displayed through an X server running on the host.

## About

BLAISE is a music discovery and streaming app. Browse a small selection of featured tracks or search by title or artist; it finds audio streams on YouTube and includes playback controls and a manual match override. Track matches are cached locally in SQLite.

## Run with Docker on Windows

1. Install Docker Desktop and an X server such as VcXsrv.
2. Start VcXsrv using XLaunch with **Multiple windows**, **Start no client**, and **Disable access control**. Allow it through Windows Firewall only on trusted private networks.
3. From this project directory, build and start the app:

   ```powershell
   docker compose up --build
   ```

The app window should appear on the Windows desktop. Close it to stop the container; run `docker compose down` to stop the Compose project. The named `blaise-data` volume keeps the SQLite cache between runs.

To remove the cached database as well, remove the volume explicitly:

```powershell
docker compose down --volumes
```

## Audio

X11 forwards the app window, not audio. To hear playback, configure a PulseAudio-compatible audio server on the host and set `PULSE_SERVER` in `docker-compose.yml` to an address reachable from the container. Without that host audio service, the GUI can run but audio may not be audible.

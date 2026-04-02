# Changelog

## v1.4.1

### Fixes

- **Fix auto-update failing on Windows** — The update script now retries until the old process fully exits before replacing the executable, preventing `Failed to load Python DLL` errors. The updater process is also fully detached to avoid being killed when the launcher closes.
- **Fix username check missing return path** — `check_username_taken()` could return `None` instead of a result dict if the API responded with an unexpected status, causing a crash in the UI.

> **Note:** If you are updating from v1.4.0 or earlier, the old auto-updater has this bug — please download this release manually from [Releases](https://github.com/Maxi49/mc-launcher/releases). Future updates will work automatically.

## v1.4.0

- CurseForge shader search integration (alongside Modrinth)
- CurseForge API key stored in `.env` file for security

## v1.3.2

- Shader search version fallback (exact -> major.minor -> no filter)

## v1.3.1

- Fix shader mod install enum error
- Fix mod toggle failing when `.disabled` file already exists on Windows

## v1.2.1

- Fix macOS SSL certificate errors with pyenv-installed Python

## v1.2.0

- Multi-instance server support (multiple worlds per version)

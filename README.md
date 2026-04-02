# MC Launcher

A lightweight, offline-capable Minecraft launcher with a modern GUI. Download, launch, and manage Minecraft client and server instances — including Fabric and Forge mod loaders, shader packs, and multi-world servers — all from a single interface.

**Platform:** Windows (primary), macOS (via GitHub Actions build)

## Installation

### Option 1: Download the executable

Go to [Releases](https://github.com/Maxi49/mc-launcher/releases) and download the latest `.exe`. No installation required — just run it.

### Option 2: Run from source

```bash
pip install PySide6 requests
python launcher_ui.py
```

## Quick Start

1. Launch the application
2. Type your username in the top bar
3. Go to the **Home** tab and select a Minecraft version from the dropdown
4. Click **Download** and wait for it to finish
5. Select the downloaded version in the top bar, choose your RAM, and click **PLAY**

That's it! Java is downloaded automatically if you don't have it installed.

---

## Features

### Home Tab — Download & Launch

This is the main tab where you download and launch Minecraft versions.

**Downloading a version:**

1. Wait for the version list to load (fetched from Mojang on startup)
2. Select the version you want (e.g. `1.21.1`)
3. Click **Download** — the launcher downloads the client JAR, libraries, and assets

**Installing mod loaders:**

- **Install Fabric** — one-click Fabric installation for any downloaded version
- **Install Forge** — downloads the Forge installer and opens it (you click "Install Client" in the Forge GUI)

After installing a mod loader, the version appears in the top dropdown as something like `fabric-loader-0.18.5-1.21.1`.

**Download options** (configured in Settings tab):

| Option | What it does |
|--------|-------------|
| Skip assets | Faster download, but no sounds or textures until you download them later |
| Include server.jar | Also downloads the server JAR alongside the client |
| Include mappings | Downloads debug mappings (useful for mod development) |
| Verify SHA1 | Validates every file's checksum — slow, but guarantees integrity |
| Show snapshots | Shows snapshot versions in the download list |

**Logs panel:**

The collapsible panel at the bottom shows download progress, build output, and launch commands in real time.

### Top Bar — Username, RAM, Version, Play

The top bar is always visible regardless of which tab you're on.

- **Username** — Your in-game name. The launcher checks if a premium account with that name exists and warns you (since offline servers may have issues with premium usernames)
- **RAM** — Memory allocated to Minecraft (2G, 4G, 6G, 8G, 12G, 16G). Can be overridden in Settings
- **Version** — Select any installed version (vanilla, Fabric, or Forge)
- **PLAY** — Launches Minecraft with the selected settings. The launcher automatically:
  - Finds or downloads the correct Java version
  - Generates an offline UUID and token for your username
  - Extracts native libraries
  - Builds the full JVM command line

### Mods Tab — Manage Your Mods

View, enable, and disable mods for the selected version.

**How it works:**

- All `.jar` files in your `mods/` folder appear as checkable items
- **Check** a mod to enable it (`.jar`)
- **Uncheck** it to disable it (renamed to `.jar.disabled` — Minecraft ignores it)
- Mods are tagged with their loader: `[Fabric]` or `[Forge]`

**Filtering:**

- **Show all versions** — shows mods for all MC versions, not just the selected one
- **Loader filter** — filter by All / Fabric / Forge / Unknown

**Buttons:**

| Button | What it does |
|--------|-------------|
| Refresh | Rescan the mods folder |
| Open mods folder | Opens the folder in your file explorer |
| Apply to Server | Copies enabled mods to your server's mods folder (smart sync — only copies mods matching the server's loader) |

### Shaders Tab — Search & Install Shader Packs

Install shader mods and browse/download shader packs from Modrinth and CurseForge.

**Step 1: Install the shader mod**

Click **Install Shader Mod** — the launcher detects your mod loader and installs the right shader mod:

| Loader | Shader mod installed |
|--------|---------------------|
| Fabric | Iris + Sodium |
| Forge | Oculus + Rubidium |
| Vanilla | Prompts you to install Fabric first |

There's also an **OptiFine Guide** button with manual installation instructions if you prefer OptiFine.

**Step 2: Download shader packs**

1. Type a shader name in the search box (e.g. "Complementary", "BSL")
2. Click **Search** or press Enter
3. Results show:
   - Source badge (`[Modrinth]` or `[CurseForge]`)
   - Title, author, categories
   - Download count
   - A **Download** button
4. Click Download — the shader pack is saved to your `shaderpacks/` folder

**Installed shader packs:**

- Listed with checkboxes to enable/disable them (same `.disabled` pattern as mods)
- Refresh and Open folder buttons available

**CurseForge integration:**

To search CurseForge in addition to Modrinth, add a CurseForge API key in Settings > API Keys. You can get one at [CurseForge for Studios](https://console.curseforge.com/).

### Server Tab — Multi-Instance Server Management

Create, configure, and launch Minecraft servers with full multi-world support.

**Creating a server:**

1. Select a Minecraft version in the top bar
2. Click **Download Server** in the Server tab
3. Optionally check **Fabric server** or **Forge server** before downloading to install a modded server

**Multi-world instances:**

Each version can have multiple server worlds:

```
servers/
  1.21.1/
    MyWorld/          ← instance 1
    SkyBlock/         ← instance 2
    Creative/         ← instance 3
```

- Click **New World** to create a new instance (enter a name)
- Select an instance from the dropdown
- Click **Delete** to remove an instance and all its data

**Launching a server:**

1. Select the instance you want
2. Click **Launch Server** (button changes to **Stop Server** while running)
3. Server logs appear in the panel below

You can run multiple server instances simultaneously — each gets its own process.

**Server options:**

| Option | Default | Description |
|--------|---------|-------------|
| Auto accept EULA | On | Automatically writes `eula=true` |
| Enable server GUI | Off | Shows the Minecraft server console GUI |
| Restart if running | Off | Restarts the server instead of showing an error |
| Offline mode | On | Sets `online-mode=false` so cracked clients can join |
| Fabric server | Off | Uses Fabric server launcher |
| Forge server | Off | Uses Forge server (mutually exclusive with Fabric) |

**Game properties:**

Configure server.properties directly from the UI:

| Property | Default | Range |
|----------|---------|-------|
| Port | 25565 | 1024–65535 |
| Difficulty | easy | peaceful, easy, normal, hard |
| Gamemode | survival | survival, creative, adventure, spectator |
| Max Players | 20 | 1–1000 |
| PvP | On | |
| Spawn Monsters | On | |
| Command Blocks | Off | |
| Allow Cheats | On | Adds you as OP level 4 |

Click **Apply to server now** to write these to `server.properties`.

**Firewall:**

Click **Open firewall port** to add a Windows firewall rule for your server port (requires admin). On macOS, the system handles this automatically.

**Connecting to your server:**

- **Same computer:** Connect to `localhost` or `127.0.0.1`
- **Local network:** Use your local IP (find it with `ipconfig` on Windows)
- **Different port:** If using a port other than 25565, connect as `ip:port`

> **Tip:** If you run multiple servers, give each one a different port (e.g. 25565, 25566, 25567) to avoid port conflicts.

### Settings Tab — Configuration

**Paths:**

| Setting | Default | Description |
|---------|---------|-------------|
| Base dir | `%APPDATA%\.minecraft` | Root directory for all Minecraft files |
| Game dir | *(empty)* | Separate directory for saves/config (optional) |
| Servers dir | *(empty)* | Root for server instances, defaults to `{base_dir}/servers` |
| Java path | *(empty)* | Custom Java executable. Leave blank for auto-detection/download |

**Launch options:**

| Setting | Description |
|---------|-------------|
| Xmx override | Override the RAM dropdown (e.g. `4G`) |
| Xms | Initial heap size (e.g. `1G`) |
| Xss | Thread stack size (e.g. `1M`) |
| Resolution | Custom window size (width x height, 0 = auto) |
| Demo mode | Launch in demo mode |
| Dry run | Show the launch command without actually running it |
| Use official JVM flags | Include Mojang's optimized G1GC flags (recommended) |

**API Keys:**

- **CurseForge API Key** — enables CurseForge shader search (stored in `.env`, not in settings)

## CLI Scripts

Every feature is also available as a standalone CLI script. Useful for automation or running on headless servers.

```bash
# Download a Minecraft version
python scripts/download_version.py 1.21.1 --base-dir ~/.minecraft

# Launch the client
python scripts/launch_client.py 1.21.1 --base-dir ~/.minecraft --username Steve --xmx 4G

# Download a server
python scripts/download_server.py 1.21.1 --servers-dir ~/.minecraft/servers

# Launch a server
python scripts/launch_server.py 1.21.1 --servers-dir ~/.minecraft/servers --accept-eula --offline-mode

# Install Fabric (client)
python scripts/install_fabric.py 1.21.1 --base-dir ~/.minecraft

# Install Fabric (server)
python scripts/install_fabric.py 1.21.1 --servers-dir ~/.minecraft/servers --server

# Install Forge
python scripts/install_forge.py 1.21.1 --base-dir ~/.minecraft

# Install shader mods
python scripts/install_shader_mod.py 1.21.1 --base-dir ~/.minecraft --loader fabric

# Fetch version manifest
python scripts/fetch_manifest.py --out versions.json
```

Run any script with `--help` for the full list of arguments.

## How Offline Mode Works

This launcher is designed for offline play — no Mojang/Microsoft account required.

- A **deterministic UUID** is generated from your username (always the same UUID for the same name)
- A **fake access token** is generated so the game runs without authentication
- Servers must have `online-mode=false` in `server.properties` for offline clients to connect (the launcher sets this automatically when "Offline mode" is checked)

## Auto-Update

The launcher checks for new releases on GitHub in the background. When an update is available, a green button appears at the bottom of the window — click it to download and install automatically.

## Project Structure

```
mc-launcher/
  launcher_ui.py           # GUI entry point
  mc_common.py             # Shared Minecraft/network library
  version.py               # Version constant
  .env                     # API keys (git-ignored)
  core/
    constants.py           # Enums and constants
    platform.py            # OS abstraction
    version_utils.py       # Version resolution and loader detection
    server_detection.py    # Server type detection
  ui/
    workers.py             # Background threads (search, download, etc.)
    process_manager.py     # QProcess lifecycle management
    style.py               # Stylesheets
  scripts/
    download_version.py    # Download client
    launch_client.py       # Launch client
    download_server.py     # Download server
    launch_server.py       # Launch server
    install_fabric.py      # Install Fabric
    install_forge.py       # Install Forge
    install_shader_mod.py  # Install shader mods
    fetch_manifest.py      # Fetch version manifest
```

## Building

To build a standalone executable:

```bash
pip install pyinstaller
python build.py
```

The output `.exe` bundles everything — no Python installation needed on the target machine.

## License

This project is not affiliated with Mojang Studios or Microsoft.

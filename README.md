# Home Monitor

A multi-location home monitoring application that collects data from various APIs and stores it in a PostgreSQL database for visualization with Grafana. Track solar power, battery storage, weather conditions, and water usage across multiple properties.

## Table of Contents

- [Features](#features)
- [Why Not Home Assistant?](#why-not-home-assistant)
- [Supported APIs](#supported-apis)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Site Configuration (sites.json)](#site-configuration-sitesjson)
- [Running the Application](#running-the-application)
  - [Docker Compose (Recommended)](#docker-compose-recommended)
  - [Local Development](#local-development)
- [API Setup](#api-setup)
  - [Teslemetry API](#teslemetry-api-tesla-fleet-api)
  - [Enphase API](#enphase-api)
  - [Enphase Local Gateway API](#enphase-local-gateway-api)
  - [OpenWeather API](#openweather-api)
  - [Tempest API](#tempest-api)
  - [Flume API](#flume-api)
  - [Tank Utility API](#tank-utility-api)
  - [iAqualink API](#iaqualink-api)
- [Grafana Dashboard](#grafana-dashboard)
- [Database Schema](#database-schema)
- [Makefile Commands](#makefile-commands)
- [Project Structure](#project-structure)
- [Testing APIs](#testing-apis)
- [Code Quality](#code-quality)

## Dashboard Screenshots

![Dashboard Screenshot 1](assets/dash%20-%2001.png)

![Dashboard Screenshot 2](assets/dash%20-%2002.png)

![Dashboard Screenshot 3](assets/dash%20-%2003.png)

![Dashboard Screenshot 4](assets/dash%20-%2004.png)

## Features

- **Multi-location support**: Track multiple homes/properties from a single configuration
- **Multiple API integrations**: Tesla/Powerwall, Enphase solar, OpenWeather, Tempest weather stations, Flume water meters, Tank Utility propane monitors, iAqualink pool controllers
- **PostgreSQL database**: Time-series data storage compatible with Grafana
- **Auto-provisioned Grafana dashboard**: Pre-configured dashboards for immediate visualization
- **Docker-ready**: Easy deployment to any Docker-capable host (Raspberry Pi, NAS, etc.)
- **Scheduled fetching**: Built-in scheduler for periodic data collection
- **System monitoring**: Automatic CPU, memory, and disk usage tracking of the fetcher host

## Why Not Home Assistant?

Home Assistant is a fantastic home automation platform, but Home Monitor serves a different purpose and use case:

### Focused on Data Collection & Historical Analysis

- **Time-series data storage**: Home Monitor stores all data in PostgreSQL, optimized for long-term historical analysis and Grafana visualization
- **Multi-location aggregation**: Designed from the ground up to track multiple properties from a single deployment, making it easy to compare performance across sites
- **Grafana-first visualization**: Pre-configured dashboards optimized for energy and resource monitoring, with powerful time-series querying capabilities

### Simpler Architecture

- **No automation engine**: Home Monitor doesn't include automations, rules, or device control—it's purely focused on data collection and visualization
- **Minimal dependencies**: Just PostgreSQL, Grafana, and a Python fetcher. No complex add-ons, integrations, or YAML configuration required
- **API-only integrations**: All integrations work directly with cloud/local APIs—no device discovery, local protocols, or MQTT setup needed

### Developer-Friendly

- **Python-based**: Easy to extend with new API integrations following established patterns
- **Version-controlled dashboards**: Dashboard configuration is generated from Python code, making changes trackable and reviewable
- **Docker-native**: Simple deployment using Docker Compose, with easy remote deployment to Raspberry Pi or other hosts

### When to Use Home Assistant Instead

Home Assistant is the better choice if you need:
- **Device automation and control** (turning lights on/off, thermostats, etc.)
- **Local device discovery** (Zigbee, Z-Wave, MQTT devices)
- **Rich UI for device management** (Home Assistant's web interface)
- **Complex automations** (sensor-triggered actions, routines, scenes)

Home Monitor is the better choice if you need:
- **Long-term data retention** with powerful querying (PostgreSQL + Grafana)
- **Multi-location monitoring** from a single deployment
- **Energy/resource analytics** with pre-built dashboards
- **Minimal maintenance** with a focused, single-purpose tool

## Supported APIs

All integrations are **optional**. Configure only the ones you have hardware/accounts for:

| API | Data Type | Description |
|-----|-----------|-------------|
| **Teslemetry** | Power, Battery | Tesla Powerwall via Tesla Fleet API (simplified OAuth) |
| **Enphase** | Solar Power | Microinverter production data (multi-app support for higher limits) |
| **Enphase Local** | Solar + Consumption | Direct gateway access for richer data including consumption |
| **OpenWeather** | Irradiance | Solar irradiance forecasts (GHI, DNI, DHI) |
| **Tempest** | Irradiance, Weather | Local weather station measurements |
| **Flume** | Water Usage | Smart water meter readings |
| **Rachio** | Sprinkler Runs | Irrigation controller watering events |
| **Tank Utility** | Propane Levels | Propane tank monitoring (fill level, temperature) |
| **iAqualink** | Pool/Spa | Pool controller monitoring (temps, pumps, heaters) |

## Quick Start

```bash
# 1. Clone and set up
git clone <repository-url>
cd home-monitor
make setup-env

# 2. Edit .env with your API credentials
vim .env

# 3. Configure your sites
vim sites.json

# 4. Start PostgreSQL and Grafana
make infra-up-local
# or: docker-compose up -d

# 5. Initialize the database
make init-db-local

# 6. Fetch data
make fetch

# 7. View dashboard at http://localhost:3000 (admin/admin)
```

## Configuration

### Environment Variables

Copy `env.example` to `.env` and configure your API credentials:

```bash
cp env.example .env
# or
make setup-env
```

#### Required Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |

#### API Credentials (Configure only for integrations you use)

All API integrations are **optional**. Only configure credentials for the services you have:

| Variable | Integration | Description |
|----------|-------------|-------------|
| `TESLEMETRY_API_KEY` | Tesla/Powerwall | Teslemetry access token |
| `OPENWEATHER_API_KEY` | OpenWeather | OpenWeather API key |
| `TEMPEST_TOKEN` | Tempest | Tempest personal access token |
| `FLUME_CLIENT_ID` | Flume | Flume API client ID |
| `FLUME_CLIENT_SECRET` | Flume | Flume API client secret |
| `FLUME_USERNAME` | Flume | Flume account email (for initial token setup) |
| `RACHIO_API_KEY` | Rachio | Rachio API key |
| `TANK_UTILITY_EMAIL` | Tank Utility | Tank Utility account email |
| `TANK_UTILITY_PASSWORD` | Tank Utility | Tank Utility account password |
| `IAQUALINK_EMAIL` | iAqualink | iAqualink account email |
| `IAQUALINK_PASSWORD` | iAqualink | iAqualink account password |

#### Enphase Credentials

Enphase supports two configuration modes. Use **multi-app mode** for higher API limits (recommended):

**Multi-App Mode** (recommended for 3x+ API limits):

| Variable | Description |
|----------|-------------|
| `ENPHASE_APP_1_API_KEY` | API key for app 1 |
| `ENPHASE_APP_1_CLIENT_ID` | OAuth client ID for app 1 |
| `ENPHASE_APP_1_CLIENT_SECRET` | OAuth client secret for app 1 |
| `ENPHASE_APP_2_API_KEY` | API key for app 2 |
| `ENPHASE_APP_2_CLIENT_ID` | OAuth client ID for app 2 |
| `ENPHASE_APP_2_CLIENT_SECRET` | OAuth client secret for app 2 |
| `ENPHASE_APP_N_*` | Continue pattern for apps 3, 4, ... N |

**Legacy Single-App Mode** (backward compatible):

| Variable | Description |
|----------|-------------|
| `ENPHASE_API_KEY` | Enphase developer API key |
| `ENPHASE_CLIENT_ID` | Enphase OAuth client ID |
| `ENPHASE_CLIENT_SECRET` | Enphase OAuth client secret |

See [Enphase API Setup](#enphase-api) for detailed configuration instructions.

#### Enphase Local Gateway Credentials (Optional)

For direct gateway access on your local network (provides consumption data):

| Variable | Description |
|----------|-------------|
| `ENPHASE_ENLIGHTEN_USERNAME` | Enlighten account email (for auto token refresh) |
| `ENPHASE_ENLIGHTEN_PASSWORD` | Enlighten account password (for auto token refresh) |

See [Enphase Local Gateway Setup](#enphase-local-gateway-api) for detailed configuration instructions.

#### Scheduler Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FETCHER_INTERVAL_MINUTES` | 5 | How often to fetch data |
| `ENPHASE_FETCH_INTERVAL_CYCLES` | 3 | Enphase calls every N cycles (saves API quota) |

#### Location-Specific Credentials (Optional)

OpenWeather and Tempest support location-specific credentials:

```bash
# Global (used for all sites)
OPENWEATHER_API_KEY=global_key

# Location-specific (overrides global for "NY" site)
OPENWEATHER_API_KEY_NY=ny_specific_key
```

#### Remote Deployment Configuration (Optional)

If you plan to deploy to a remote host (Raspberry Pi, etc.), you can configure these in your `.env` file to keep your personal settings private:

```bash
# Remote host SSH connection string
DEPLOY_HOST=pi@raspberry-pi.local

# Remote host project directory path (absolute path)
DEPLOY_CONFIG_PATH=/home/pi/home-monitor
```

These values are automatically read by the Makefile. If not set, defaults are used (see `env.example` for details).

### Site Configuration (sites.json)

Copy `sites.example.json` to `sites.json` and configure your locations:

```bash
cp sites.example.json sites.json
# or use make setup-env to copy both .env and sites.json
```

Define your locations and API configurations in `sites.json`. All integrations are optional—only configure what you have:

```json
{
  "sites": {
    "Minimal": {
      "capacity_kw": 10.0,
      "location": {
        "latitude": 40.7128,
        "longitude": -74.0060
      }
    },
    "FullyLoaded": {
      "capacity_kw": 15.4,
      "tempest": {
        "station_id": 12345
      },
      "openweather": {
        "latitude": 34.0522,
        "longitude": -118.2437
      },
      "enphase": {
        "site_id": 1234567
      },
      "enphase_local": {
        "gateways": [
          {"serial": "123456789012", "host": "192.168.1.100"}
        ]
      },
      "tesla": {
        "site_ids": ["your_tesla_site_id_1", "your_tesla_site_id_2"]
      },
      "flume": {
        "device_id": "your_flume_device_id_here"
      },
      "rachio": {
        "device_id": "abc123"
      },
      "tankutility": {
        "device_id": "def456"
      },
      "iaqualink": {
        "serial_number": "your_pool_controller_serial",
        "device_name": "Pool Controller"
      }
    }
  }
}
```

#### Required Fields Per Site

| Field | Description |
|-------|-------------|
| `capacity_kw` | Solar system capacity in kW |
| Location coordinates | Either via `openweather.latitude/longitude` or `location.latitude/longitude` |

#### Optional API Integrations

All API integrations are optional. Configure only the ones you have hardware/accounts for:

| Field | Description |
|-------|-------------|
| `tempest.station_id` | Tempest weather station ID |
| `openweather.latitude/longitude` | Site coordinates for irradiance forecast |
| `enphase.site_id` | Enphase system ID (cloud API) |
| `enphase_local.gateways` | Array of local gateway configs (serial + host) |
| `tesla.site_ids` | Array of Tesla energy site IDs |
| `flume.device_id` | Flume water meter device ID |
| `rachio.device_id` | Rachio sprinkler controller device ID |
| `tankutility.device_id` | Tank Utility propane monitor device ID |
| `iaqualink.serial_number` | iAqualink pool controller serial number |
| `iaqualink.device_name` | iAqualink device name (alternative to serial_number) |

If you don't use OpenWeather but need coordinates for database storage, use:
```json
"location": {
  "latitude": 40.7128,
  "longitude": -74.0060
}
```

## Running the Application

### Docker Compose (Recommended)

Docker Compose includes three services:
- **postgres**: PostgreSQL database (starts automatically)
- **grafana**: Dashboard UI at http://localhost:3000 (starts automatically)
- **fetcher**: Data fetcher (manual profile - start explicitly)

```bash
# Start database and Grafana
docker-compose up -d
# or
make infra-up-local

# View logs
make infra-logs-local

# Stop all services
docker-compose down
# or
make infra-down-local
```

#### Running the Fetcher

The fetcher is in a `manual` profile to prevent unintended API calls:

```bash
# One-time fetch (runs once and exits)
docker-compose --profile manual up fetcher
# or
make fetcher-once

# Scheduled fetching (runs every 5 minutes)
docker-compose --profile scheduled up -d fetcher-scheduled
# or
make fetcher-start-local

# Stop scheduled fetcher
make fetcher-stop-local
```

#### Grafana Access

- **URL**: http://localhost:3000
- **Username**: `admin`
- **Password**: `admin`
- The PostgreSQL data source and dashboard are automatically provisioned

### Local Development

```bash
# Install dependencies
make deps
# or
pip install -r requirements.txt

# Initialize database
make init-db-local

# Run one-time fetch
make fetch

# Run HTTP server
make server
```

### Remote Deployment (Raspberry Pi)

Deploy to a Raspberry Pi or any SSH-accessible host using Docker Context. This builds and runs containers directly on the remote machine—no registry or image transfer needed.

#### Prerequisites

1. **SSH key-based auth** to your Pi (password-less login)
2. **Docker installed** on the Pi
3. **Docker Compose plugin** installed on the Pi

```bash
# If you haven't set up SSH keys:
ssh-keygen -t ed25519  # Generate key (skip if you have one)
ssh-copy-id pi@raspberry-pi.local  # Copy to Pi

# Verify you can connect without a password:
ssh pi@raspberry-pi.local "docker --version"
```

#### One-Time Setup

Configure the remote host in your `.env` file (recommended) or override via command line:

```bash
# In .env file (keeps your personal settings private)
DEPLOY_HOST=pi@raspberry-pi.local
DEPLOY_CONFIG_PATH=/home/pi/home-monitor
```

Or override via command line:
```bash
make deploy-setup DEPLOY_HOST=pi@192.168.1.100
```

Then create the Docker context:

```bash
make deploy-setup
# Or with a custom host:
make deploy-setup DEPLOY_HOST=pi@192.168.1.100
```

#### Deploying

```bash
# Deploy (builds on Pi and starts containers)
make deploy-remote

# View logs
make infra-logs-remote

# Check status
make infra-ps-remote

# Stop everything
make infra-down-remote
```

#### Managing the Fetcher

```bash
# Start scheduled fetcher (runs every 5 minutes)
make fetcher-start-remote

# View fetcher logs
make fetcher-logs-remote

# Stop scheduled fetcher
make fetcher-stop-remote
```

#### Other Commands

```bash
# Restart without rebuilding
make infra-restart-remote

# Open shell in a container
make deploy-exec-remote SERVICE=postgres

# Check connection status
make deploy-check-remote

# Remove the Docker context
make deploy-clean-remote
```

#### Configuration Files on Pi

The `.env` and `sites.json` files must exist on the Pi in the project directory. You can:

1. **Clone the repo on the Pi** and copy config files there
2. **Use `scp`** to copy files: `scp .env sites.json pi@raspberry-pi.local:~/home-monitor/`
3. **Edit directly on the Pi** via SSH

The Docker Context approach runs `docker compose` on the Pi, so it reads config files from the Pi's filesystem.

### Docker Standalone (Without Compose)

When running the Docker image without docker-compose (e.g., on a remote server), you must provide environment variables and configuration files manually. The `.env` file is intentionally excluded from the Docker image for security—secrets should never be baked into images.

#### Option 1: Using `--env-file`

```bash
# Build the image
docker build -t home-monitor .

# Run with env file and sites.json mounted
docker run --rm \
  --env-file .env \
  -v $(pwd)/sites.json:/app/sites.json:ro \
  home-monitor python -m home_monitor.fetcher
```

#### Option 2: Using individual `-e` flags

```bash
docker run --rm \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -e TESLEMETRY_API_KEY=your_key \
  -e OPENWEATHER_API_KEY=your_key \
  -v $(pwd)/sites.json:/app/sites.json:ro \
  home-monitor python -m home_monitor.fetcher
```

#### Required for standalone operation:
- **Environment variables**: Pass via `--env-file .env` or individual `-e` flags
- **sites.json**: Mount via `-v /path/to/sites.json:/app/sites.json:ro`
- **Network access**: Ensure the container can reach your PostgreSQL database

## API Setup

### Teslemetry API (Tesla Fleet API)

Teslemetry provides simplified access to the Tesla Fleet API without direct OAuth setup.

1. **Sign up** at https://teslemetry.com/login (uses Tesla authentication)
2. **Get API key** from https://teslemetry.com/console → Access Tokens
3. **Find Energy Site IDs** in the Teslemetry Console
4. **Configure**:
   ```bash
   # In .env
   TESLEMETRY_API_KEY=your_api_key_here
   ```
   ```json
   // In sites.json
   "tesla": {
     "site_ids": ["your_site_id_1", "your_site_id_2"]
   }
   ```

**Note**: Multiple `site_ids` are supported per location (e.g., multiple Powerwall banks).

### Enphase API

Enphase uses OAuth 2.0 with a one-time authorization flow. The system supports **multiple apps** to increase API rate limits. Most configurations will likely desire the multi-app configuration even though it takes a few more minutes to setup / configure.

Per-app config:
![Enphase per-app configuration](assets/enphase-perapp-config.png)

Multiple apps configured:
![Multiple Enphase apps configured](assets/enphase-multiple-apps.png)

#### Single App Setup (Legacy)

1. **Register** at https://developer-v4.enphase.com/
2. **Create an application** (Developer app with Watt/Kilowatt/Megawatt plan)
3. **Configure credentials** in `.env`:
   ```bash
   ENPHASE_API_KEY=your_api_key
   ENPHASE_CLIENT_ID=your_client_id
   ENPHASE_CLIENT_SECRET=your_client_secret
   ```
4. **One-time token setup**:
   ```bash
   # Generate authorization URL
   make enphase-authorize
   
   # Visit the URL, authorize, copy the code from redirect URL
   
   # Exchange code for tokens (stored in database)
   make enphase-exchange CODE=your_authorization_code
   ```
5. **Add to sites.json**:
   ```json
   "enphase": {
     "site_id": 12345
   }
   ```

#### Multi-App Setup (Recommended for Higher API Limits)

Enphase's free tier limits API calls to 1,000/month per app. To increase limits, create multiple apps and the system will automatically rotate between them.

1. **Create N apps** at https://developer-v4.enphase.com/ (e.g., 3 apps for 3,000 calls/month)
2. **Configure each app** in `.env`:
   ```bash
   # App 1
   ENPHASE_APP_1_API_KEY=your_api_key_1
   ENPHASE_APP_1_CLIENT_ID=your_client_id_1
   ENPHASE_APP_1_CLIENT_SECRET=your_client_secret_1
   
   # App 2
   ENPHASE_APP_2_API_KEY=your_api_key_2
   ENPHASE_APP_2_CLIENT_ID=your_client_id_2
   ENPHASE_APP_2_CLIENT_SECRET=your_client_secret_2
   
   # App 3 (add as many as needed)
   ENPHASE_APP_3_API_KEY=your_api_key_3
   ENPHASE_APP_3_CLIENT_ID=your_client_id_3
   ENPHASE_APP_3_CLIENT_SECRET=your_client_secret_3
   ```
3. **Authorize each app** (one-time per app):
   ```bash
   # App 1
   make enphase-authorize APP=1
   # Visit URL, authorize, get code
   make enphase-exchange APP=1 CODE=xxxxx
   
   # Repeat for apps 2, 3, etc.
   make enphase-authorize APP=2
   make enphase-exchange APP=2 CODE=yyyyy
   ```
4. **Check status**:
   ```bash
   make enphase-list-apps
   ```

**Multi-App Features**:
- **Round-robin rotation**: API calls are distributed evenly across apps
- **Automatic failover**: If one app hits rate limits, the system tries the next
- **Per-app token refresh**: Each app's tokens are managed independently
- **API call tracking**: Daily call counts per app are tracked in the database

**Token Management**: Tokens are stored in the database and refreshed automatically. The API key, client ID, and secret must remain in `.env` for token refresh.

### Enphase Local Gateway API

The Enphase Local Gateway API provides direct access to your IQ Gateway on the local network. This offers **richer data than the cloud API**, including:

- **Real-time consumption data** (not available via cloud API)
- **Grid voltage and frequency per phase**
- **Detailed meter readings**
- **No API rate limits** (local network only)

**Requirements**:
- Network access to your Enphase IQ Gateway
- Gateway token (valid for 1 year for system owners)

#### Setup

1. **Find your gateway**:
   - Gateway serial number is printed on the device (12 digits)
   - Gateway IP can be found in your router's DHCP client list
   - Default hostname is often `envoy.local` or `envoy-SERIALNUMBER.local`

2. **Get your initial token**:
   - Visit https://entrez.enphaseenergy.com
   - Log in with your Enlighten account
   - Select your system and generate a token

3. **Store the token**:
   ```bash
   make enphase-gateway-store SERIAL=123456789012 HOST=192.168.1.100 TOKEN="eyJ..."
   ```

4. **Configure in sites.json**:
   ```json
   "enphase_local": {
     "gateways": [
       {"serial": "123456789012", "host": "192.168.1.100"}
     ]
   }
   ```

5. **Test connectivity**:
   ```bash
   make enphase-gateway-test SERIAL=123456789012
   ```

#### Automatic Token Refresh

Tokens are valid for 1 year but can be refreshed automatically. Configure your Enlighten credentials to enable auto-refresh:

```bash
# In .env
ENPHASE_ENLIGHTEN_USERNAME=your_enlighten_email@example.com
ENPHASE_ENLIGHTEN_PASSWORD=your_enlighten_password_here
```

With credentials configured:
- Tokens are automatically refreshed when within 30 days of expiration
- The fetcher checks token expiration on each cycle
- No manual intervention needed after initial setup

#### Manual Token Management

```bash
# List all gateway tokens and their status
make enphase-gateway-list

# Manually refresh a specific token
make enphase-gateway-refresh SERIAL=123456789012

# Refresh all gateway tokens
make enphase-gateway-refresh ALL=1

# Refresh tokens expiring within N days
make enphase-gateway-refresh-expiring DAYS=60

# Delete a token
make enphase-gateway-delete SERIAL=123456789012
```

#### Multiple Gateways

If you have multiple gateways (e.g., at different locations), configure each one:

```json
"enphase_local": {
  "gateways": [
    {"serial": "123456789012", "host": "192.168.1.100"},
    {"serial": "234567890123", "host": "192.168.1.101"}
  ]
}
```

**Data collected**:
- Power produced (W)
- Power consumed (W)
- Net power (W) - negative = exporting, positive = importing
- Grid voltage L1/L2 (V)
- Grid frequency (Hz)
- Energy produced today (Wh)
- Energy consumed today (Wh)
- Lifetime energy (Wh)

### OpenWeather API

1. **Sign up** at https://openweathermap.org/
2. **Subscribe** to Solar Irradiance API
3. **Configure** in `.env`:
   ```bash
   OPENWEATHER_API_KEY=your_api_key
   ```
4. **Add coordinates** to `sites.json` (required for all sites):
   ```json
   "openweather": {
     "latitude": 42.0500,
     "longitude": -73.8983
   }
   ```

### Tempest API

1. **Log in** at https://tempestwx.com/
2. **Create token**: Settings → Data Authorizations → Create Token
3. **Get Station ID**: Settings → Stations
4. **Configure** in `.env`:
   ```bash
   TEMPEST_TOKEN=your_personal_access_token
   ```
5. **Add to sites.json** (required for all sites):
   ```json
   "tempest": {
     "station_id": 35943
   }
   ```

### Flume API

Flume uses OAuth 2 with username/password authentication.

1. **Create API credentials** at https://portal.flumewater.com/ → Settings → API Access
2. **Configure** in `.env`:
   ```bash
   FLUME_CLIENT_ID=your_client_id
   FLUME_CLIENT_SECRET=your_client_secret
   FLUME_USERNAME=your_email@example.com
   ```
3. **Get tokens** (you'll be prompted for password):
   ```bash
   make flume-token
   ```
4. **List devices** to find your device ID:
   ```bash
   make flume-devices
   ```
5. **Add to sites.json**:
   ```json
   "flume": {
     "device_id": "your_device_id"
   }
   ```

### Rachio API

Rachio tracks sprinkler watering events from your irrigation controller.

1. **Get API key** at https://app.rach.io/ → Account Settings → Get API Key
2. **Configure** in `.env`:
   ```bash
   RACHIO_API_KEY=your_api_key
   ```
3. **Find device ID**: The device ID can be found in the Rachio app or by using the API
4. **Add to sites.json**:
   ```json
   "rachio": {
     "device_id": "your_device_id"
   }
   ```

### Tank Utility API

Tank Utility monitors propane tank levels using cellular-connected sensors.

**Note**: Tank Utility was acquired by ANOVA and is no longer part of Generac. The legacy API may eventually be deprecated.

1. **Get credentials**: Use the same email/password you use to log into the Tank Utility app at https://app.tankutility.com/
2. **Configure** in `.env`:
   ```bash
   TANK_UTILITY_EMAIL=your_email@example.com
   TANK_UTILITY_PASSWORD=your_password
   ```
3. **Find device ID**: Run the test service to list your devices:
   ```bash
   make test-service SERVICE=tankutility
   ```
4. **Add to sites.json**:
   ```json
   "tankutility": {
     "device_id": "your_device_id"
   }
   ```

**Data collected**:
- Tank fill level (percentage and estimated gallons)
- Tank capacity
- Temperature
- Battery status (good/low/critical)
- Fuel type

### iAqualink API

iAqualink monitors pool and spa equipment from Zodiac/Jandy pool controllers.

1. **Get credentials**: Use the same email/password you use to log into the iAqualink app at https://www.iaqualink.com/
2. **Configure** in `.env`:
   ```bash
   IAQUALINK_EMAIL=your_email@example.com
   IAQUALINK_PASSWORD=your_password
   ```
3. **Find device serial number**: Run the test service to list your devices:
   ```bash
   make test-service SERVICE=iaqualink
   ```
4. **Add to sites.json** (use either `serial_number` or `device_name`):
   ```json
   "iaqualink": {
     "serial_number": "your_pool_controller_serial"
   }
   ```
   Or by device name:
   ```json
   "iaqualink": {
     "device_name": "Pool Controller"
   }
   ```

**Data collected**:
- Pool temperature
- Spa temperature
- Air temperature
- Pool/spa heater set points
- Pool pump status (on/off)
- Spa pump status (on/off)
- Pool heater status (on/off)
- Spa heater status (on/off)

## Grafana Dashboard

A pre-configured dashboard is automatically provisioned with panels for:

- Power production/consumption by site
- Irradiance vs actual production comparison
- Grid import/export
- Battery state of charge
- Water usage

### Updating the Dashboard

The dashboard is generated from Python, not edited directly:

```bash
# Edit the generator script
vim scripts/generate_dashboard.py

# Regenerate the dashboard JSON
make generate-dashboard-local

# Refresh Grafana in your browser (no restart needed)
```

**Important**: Do not edit `grafana/provisioning/dashboards/home-monitor.json` directly—it will be overwritten.

## Database Schema

| Table | Description |
|-------|-------------|
| `locations` | Physical locations with coordinates and capacity |
| `location_api_configs` | API configuration per location (legacy tokens stored here) |
| `enphase_app_tokens` | Per-app Enphase tokens for multi-app mode |
| `enphase_gateway_tokens` | Enphase local gateway tokens (auto-refreshed) |
| `power_readings` | Power production/consumption time-series |
| `enphase_local_readings` | Local gateway data (production, consumption, grid) |
| `battery_readings` | Battery state and charge/discharge data |
| `battery_banks` | Battery bank metadata from Tesla site_info |
| `irradiance_readings` | Solar irradiance measurements |
| `water_readings` | Water usage from Flume meters |
| `sprinkler_runs` | Rachio sprinkler watering events |
| `propane_readings` | Propane tank levels from Tank Utility |
| `pool_readings` | Pool/spa data from iAqualink |
| `system_readings` | Host/container CPU, memory, and disk usage |

All time-series tables include `timestamp` and `location_id` for efficient Grafana queries. The `system_readings` table is an exception—it tracks the fetcher host, not a specific location.

## Makefile Commands

### Setup & Development (Local)

| Command | Description |
|---------|-------------|
| `make deps` | Install Python dependencies |
| `make setup-env` | Copy env.example to .env and sites.json |
| `make init-db-local` | Initialize database schema |
| `make drop-db-local` | Drop all tables (reset database) |
| `make db-dump` | Dump database to /tmp/home_monitor_dump.sql |
| `make format` | Auto-format code (black, isort) |
| `make lint` | Check code style (flake8) |
| `make test-format` | Format code and then lint (for CI) |
| `make clean` | Remove Python cache files |
| `make build` | Build Docker image |

### Running (Local)

| Command | Description |
|---------|-------------|
| `make fetch` | Run data fetcher once |
| `make run` | Alias for fetch |
| `make server` | Start HTTP server |
| `make test-service SERVICE=<name>` | Test a specific API service (see Testing APIs section) |

### Infrastructure (Local)

| Command | Description |
|---------|-------------|
| `make infra-up-local` | Start postgres and grafana |
| `make infra-down-local` | Stop all services |
| `make infra-logs-local` | View logs |
| `make fetcher-once` | Run fetcher once (foreground) |
| `make fetcher-once-bg` | Run fetcher once (background) |
| `make fetcher-once-logs` | View one-time fetcher logs |
| `make fetcher-start-local` | Start scheduled fetcher |
| `make fetcher-stop-local` | Stop scheduled fetcher |
| `make fetcher-logs-local` | View scheduled fetcher logs |

### API Token Management (Local)

| Command | Description |
|---------|-------------|
| `make enphase-list-apps` | List configured Enphase apps and status |
| `make enphase-authorize [APP=N]` | Generate Enphase OAuth URL |
| `make enphase-exchange CODE=xxx [APP=N]` | Exchange auth code for tokens |
| `make enphase-refresh REFRESH_TOKEN=xxx [APP=N]` | Manually refresh a token |
| `make flume-token` | Get Flume tokens (prompts for password) |
| `make flume-refresh REFRESH_TOKEN=xxx` | Manually refresh Flume token |
| `make flume-devices` | List Flume devices |

### Enphase Gateway Token Management (Local)

| Command | Description |
|---------|-------------|
| `make enphase-gateway-store SERIAL=x HOST=x TOKEN=x` | Store a gateway token |
| `make enphase-gateway-list` | List all gateway tokens and expiration |
| `make enphase-gateway-test SERIAL=x` | Test gateway connectivity |
| `make enphase-gateway-refresh SERIAL=x` | Refresh a specific token |
| `make enphase-gateway-refresh ALL=1` | Refresh all gateway tokens |
| `make enphase-gateway-refresh-expiring [DAYS=N]` | Refresh expiring tokens (default: 30 days) |
| `make enphase-gateway-delete SERIAL=x` | Delete a gateway token |
| `make enphase-gateway-init` | Fetch tokens from Enlighten for all gateways in sites.json that don't have tokens |

### Infrastructure (Remote)

| Command | Description |
|---------|-------------|
| `make deploy-setup` | One-time: create Docker context for remote host |
| `make deploy-remote` | Deploy to remote (build and start containers) |
| `make deploy-build-remote` | Build images on remote without starting |
| `make deploy-check-remote` | Test connection to remote Docker |
| `make deploy-sync-remote` | Sync config files to remote |
| `make deploy-pull-remote` | Pull latest images on remote host (if using registry) |
| `make infra-up-remote` | Start postgres and grafana |
| `make infra-down-remote` | Stop containers |
| `make infra-logs-remote` | View logs |
| `make infra-ps-remote` | Show container status |
| `make infra-restart-remote` | Restart containers (no rebuild) |
| `make fetcher-start-remote` | Start scheduled fetcher |
| `make fetcher-stop-remote` | Stop scheduled fetcher |
| `make fetcher-logs-remote` | View fetcher logs |
| `make init-db-remote` | Initialize database schema |
| `make drop-db-remote` | Drop all tables |
| `make db-migrate-to-remote` | Migrate database from local to remote (dump + copy + restore) |
| `make db-restore-remote` | Restore database dump to remote host (run db-dump first) |
| `make deploy-exec-remote SERVICE=x` | Open shell in container |
| `make deploy-clean-remote` | Remove Docker context |
| `make deploy-fix-permissions-remote` | Fix permissions on remote config directory (requires sudo) |
| `make redeploy-fetcher-remote` | Init DB, rebuild fetcher, and restart scheduled fetcher |
| `make enphase-gateway-init-remote` | Fetch tokens from Enlighten for all gateways in sites.json that don't have tokens |

### Dashboard

| Command | Description |
|---------|-------------|
| `make generate-dashboard-local` | Regenerate Grafana dashboard JSON (local) |
| `make generate-dashboard-remote` | Regenerate and sync dashboard (remote) |

### HTTPS Configuration (Remote)

| Command | Description |
|---------|-------------|
| `make deploy-enable-https-remote` | Enable HTTPS on Grafana (generates certs if needed, restarts Grafana) |
| `make deploy-disable-https-remote` | Disable HTTPS on Grafana (switch back to HTTP) |
| `make deploy-generate-certs-remote` | Generate self-signed TLS certificates for Grafana HTTPS |

## Project Structure

```
home-monitor/
├── home_monitor/           # Main package
│   ├── apis/               # API clients
│   │   ├── tesla.py
│   │   ├── enphase.py
│   │   ├── enphase_local.py  # Local gateway API
│   │   ├── openweather.py
│   │   ├── tempest.py
│   │   ├── flume.py
│   │   ├── rachio.py
│   │   ├── tankutility.py
│   │   └── iaqualink.py
│   ├── config.py           # Credential management
│   ├── database.py         # Database schema and operations
│   ├── enphase_app_manager.py  # Multi-app Enphase rotation & failover
│   ├── fetcher.py          # Data fetching orchestration
│   ├── scheduler.py        # Scheduled fetching
│   ├── server.py           # HTTP server for triggering fetches
│   ├── site_config.py      # sites.json configuration
│   └── token_manager.py    # OAuth token refresh
├── scripts/                # Utility scripts
│   ├── generate_dashboard.py
│   ├── get_enphase_token.py
│   ├── get_flume_token.py
│   ├── manage_gateway_tokens.py  # Enphase local gateway tokens
│   ├── rachio_backfill.py
│   └── test_service.py
├── grafana/                # Grafana provisioning
├── sites.json              # Site configuration (create from sites.example.json)
├── sites.example.json      # Example site configuration
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── requirements.txt
└── env.example
```

## Testing APIs

Test individual API integrations without affecting the database:

```bash
# Test Tesla API
make test-service SERVICE=tesla

# Test with specific site ID (no database required)
make test-service SERVICE=tesla ENERGY_SITE_ID=your_site_id

# Test Enphase
make test-service SERVICE=enphase LOCATION=FL

# Test OpenWeather with coordinates
make test-service SERVICE=openweather LAT=42.05 LON=-73.89

# Test Tempest
make test-service SERVICE=tempest STATION_ID=35943

# Save results to database
make test-service SERVICE=openweather SAVE_TO_DB=1

# Hide raw API response
make test-service SERVICE=tesla HIDE_RAW=1
```

## Code Quality

The project uses `black`, `isort`, and `flake8`:

```bash
make format    # Auto-format code
make lint      # Check code style
make test-format  # Format then lint
```

Configuration files:
- `.flake8` - Flake8 rules
- `pyproject.toml` - Black and isort settings

## Security Best Practices

1. **Never commit `.env` or `sites.json`** — they are in `.gitignore`
2. **Tokens in database** — Enphase/Flume tokens stored in database
3. **Rotate credentials regularly** — update when tokens expire
4. **Use location-specific credentials when needed** — isolate per site (OpenWeather/Tempest)

## License

MIT

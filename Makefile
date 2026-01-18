# Load .env file if it exists (for local deployment configuration)
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

# Project configuration
DATABASE_URL ?= postgresql://home_monitor:home_monitor_password@localhost:5432/home_monitor
IMAGE_NAME ?= home-monitor
IMAGE_TAG ?= latest

# Remote deployment configuration
# These can be set in .env file to keep your personal values private
# Defaults are provided for open source users
DEPLOY_HOST ?= pi@raspberry-pi.local
DEPLOY_CONFIG_PATH ?= /home/pi/home-monitor

# Helper for running docker compose on remote via SSH
# Usage: $(REMOTE_COMPOSE) up -d"  (note: must end command with closing quote)
REMOTE_COMPOSE = ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml

# Legacy: Docker context (kept for compatibility but SSH is preferred for bind mounts)
DEPLOY_CONTEXT ?= home-monitor-pi
DEPLOY_COMPOSE := -p home-monitor -f docker-compose.prod.yml

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help:  ## 📖 Show this help message and exit
	@echo ""
	@echo "\033[1;33m📍 Local Commands\033[0m"
	@echo "\033[1;33m─────────────────\033[0m"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## \[local\]' Makefile | sort | sed -E 's/^([a-zA-Z0-9_-]+):.*## \[local\] (.*)/\1\t\2/' | awk -F'\t' '{printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "\033[1;35m🌐 Remote Commands\033[0m"
	@echo "\033[1;35m──────────────────\033[0m"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## \[remote\]' Makefile | sort | sed -E 's/^([a-zA-Z0-9_-]+):.*## \[remote\] (.*)/\1\t\2/' | awk -F'\t' '{printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo ""

# =============================================================================
# Local Commands
# =============================================================================

build:  ## [local] 🐳 Build Docker image
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

clean:  ## [local] 🧹 Clean up Python cache files
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true

db-dump:  ## [local] 💾 Dump database to /tmp/home_monitor_dump.sql
	@echo "💾 Dumping local database..."
	docker exec home-monitor-db pg_dump -U home_monitor -d home_monitor --no-owner --no-acl > /tmp/home_monitor_dump.sql
	@echo "✅ Dump complete: $$(wc -l < /tmp/home_monitor_dump.sql) lines, $$(du -h /tmp/home_monitor_dump.sql | cut -f1)"

deps:  ## [local] 📦 Install Python dependencies
	pip install -r requirements.txt

drop-db-local:  ## [local] 🗑️  Drop all database tables
	@echo "Dropping all database tables..."
	@PGPASSWORD=home_monitor_password psql -h localhost -U home_monitor -d home_monitor -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" || echo "⚠️  Could not connect to database. Make sure PostgreSQL is running (try 'make infra-up-local')."

enphase-authorize:  ## [local] 🔐 Generate Enphase OAuth authorization URL (usage: make enphase-authorize [APP=N])
	@PYTHONPATH=. python scripts/get_enphase_token.py --authorize-url $(if $(APP),--app $(APP))

enphase-gateway-store:  ## [local] 🔑 Store an Enphase local gateway token (usage: make enphase-gateway-store SERIAL=xxx HOST=ip TOKEN=xxx)
	@if [ -z "$(SERIAL)" ] || [ -z "$(HOST)" ] || [ -z "$(TOKEN)" ]; then \
		echo "❌ ERROR: SERIAL, HOST, and TOKEN are required"; \
		echo ""; \
		echo "Usage: make enphase-gateway-store SERIAL=<serial> HOST=<ip> TOKEN=<token> [EXPIRES=<timestamp>]"; \
		echo ""; \
		echo "Options:"; \
		echo "  SERIAL   - Gateway serial number (12 digits, required)"; \
		echo "  HOST     - Gateway IP address or hostname (required)"; \
		echo "  TOKEN    - Gateway access token (required)"; \
		echo "  EXPIRES  - Token expiration (ISO or Unix timestamp, optional, default: 1 year)"; \
		echo ""; \
		echo "Example:"; \
		echo "  make enphase-gateway-store SERIAL=123456789012 HOST=192.168.1.100 TOKEN='eyJ...'"; \
		echo ""; \
		echo "Get a token at: https://entrez.enphaseenergy.com"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py store \
		--serial "$(SERIAL)" --host "$(HOST)" --token "$(TOKEN)" \
		$(if $(EXPIRES),--expires-at "$(EXPIRES)")

enphase-gateway-list:  ## [local] 📋 List all stored Enphase gateway tokens
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py list

enphase-gateway-test:  ## [local] 🧪 Test connectivity to an Enphase local gateway (usage: make enphase-gateway-test SERIAL=xxx)
	@if [ -z "$(SERIAL)" ]; then \
		echo "❌ ERROR: SERIAL is required"; \
		echo "Usage: make enphase-gateway-test SERIAL=<gateway_serial>"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py test --serial "$(SERIAL)"

enphase-gateway-delete:  ## [local] 🗑️  Delete an Enphase gateway token (usage: make enphase-gateway-delete SERIAL=xxx)
	@if [ -z "$(SERIAL)" ]; then \
		echo "❌ ERROR: SERIAL is required"; \
		echo "Usage: make enphase-gateway-delete SERIAL=<gateway_serial>"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py delete --serial "$(SERIAL)"

enphase-gateway-refresh:  ## [local] 🔄 Refresh an Enphase gateway token (usage: make enphase-gateway-refresh SERIAL=xxx or make enphase-gateway-refresh ALL=1)
	@if [ -z "$(SERIAL)" ] && [ -z "$(ALL)" ]; then \
		echo "❌ ERROR: SERIAL or ALL=1 is required"; \
		echo ""; \
		echo "Usage:"; \
		echo "  make enphase-gateway-refresh SERIAL=<serial>  # Refresh specific gateway"; \
		echo "  make enphase-gateway-refresh ALL=1            # Refresh all gateways"; \
		echo ""; \
		echo "Requires ENPHASE_ENLIGHTEN_USERNAME and ENPHASE_ENLIGHTEN_PASSWORD in .env"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py refresh \
		$(if $(ALL),--all,--serial "$(SERIAL)")

enphase-gateway-refresh-expiring:  ## [local] 🔄 Refresh gateway tokens expiring within N days (default: 30)
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py refresh-expiring \
		$(if $(DAYS),--days $(DAYS))

enphase-gateway-init:  ## [local] 🔑 Fetch tokens from Enlighten for all gateways in sites.json that don't have tokens
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_gateway_tokens.py init

enphase-gateway-init-remote:  ## [remote] 🔑 Fetch tokens from Enlighten for all gateways in sites.json that don't have tokens
	@echo "🔑 Initializing Enphase gateway tokens on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_gateway_tokens.py init"

enphase-exchange-remote:  ## [remote] 🔄 Exchange Enphase authorization code for access token (usage: make enphase-exchange-remote CODE=code [APP=N])
	@if [ -z "$(CODE)" ]; then \
		echo "❌ ERROR: CODE parameter is required"; \
		echo ""; \
		echo "Usage: make enphase-exchange-remote CODE=your_authorization_code [APP=N]"; \
		echo ""; \
		echo "Options:"; \
		echo "  CODE  - Authorization code from the redirect URL (required)"; \
		echo "  APP   - App index for multi-app mode (1, 2, 3, ...). Omit for legacy single-app."; \
		echo ""; \
		echo "Examples:"; \
		echo "  make enphase-exchange-remote CODE=xxxxx APP=1    # Multi-app mode"; \
		echo "  make enphase-exchange-remote CODE=xxxxx          # Legacy single-app mode"; \
		exit 1; \
	fi
	@echo "🔄 Exchanging Enphase authorization code on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "docker exec home-monitor-fetcher-scheduled python scripts/get_enphase_token.py --exchange-code '$(CODE)' $(if $(APP),--app $(APP))"

enphase-list-apps-remote:  ## [remote] 📋 List configured Enphase apps and their status
	@echo "📋 Listing Enphase apps on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "docker exec home-monitor-fetcher-scheduled python scripts/get_enphase_token.py --list-apps"

enphase-exchange:  ## [local] 🔄 Exchange Enphase authorization code for access token (usage: make enphase-exchange CODE=code [APP=N])
	@if [ -z "$(CODE)" ]; then \
		echo "❌ ERROR: CODE parameter is required"; \
		echo ""; \
		echo "Usage: make enphase-exchange CODE=your_authorization_code [APP=N]"; \
		echo ""; \
		echo "Options:"; \
		echo "  CODE  - Authorization code from the redirect URL (required)"; \
		echo "  APP   - App index for multi-app mode (1, 2, 3, ...). Omit for legacy single-app."; \
		echo ""; \
		echo "Examples:"; \
		echo "  make enphase-exchange CODE=xxxxx APP=1    # Multi-app mode"; \
		echo "  make enphase-exchange CODE=xxxxx          # Legacy single-app mode"; \
		echo ""; \
		echo "Get the authorization code from the redirect URL after visiting the authorization URL"; \
		echo "(Run 'make enphase-authorize [APP=N]' first to get the authorization URL)"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_enphase_token.py --exchange-code "$(CODE)" $(if $(APP),--app $(APP)) $(if $(LOCATION_ID),--location-id $(LOCATION_ID))

enphase-list-apps:  ## [local] 📋 List configured Enphase apps and their status
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_enphase_token.py --list-apps

enphase-refresh:  ## [local] 🔄 Refresh Enphase access token (usage: make enphase-refresh REFRESH_TOKEN=token [APP=N])
	@if [ -z "$(REFRESH_TOKEN)" ]; then \
		echo "❌ ERROR: REFRESH_TOKEN parameter is required"; \
		echo ""; \
		echo "Usage: make enphase-refresh REFRESH_TOKEN=your_refresh_token [APP=N]"; \
		echo ""; \
		echo "Options:"; \
		echo "  REFRESH_TOKEN  - Refresh token (required)"; \
		echo "  APP            - App index for multi-app mode (1, 2, 3, ...). Omit for legacy."; \
		echo ""; \
		echo "Note: Tokens are automatically refreshed during API calls. Manual refresh is rarely needed."; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_enphase_token.py --refresh-token "$(REFRESH_TOKEN)" $(if $(APP),--app $(APP))

fetch:  ## [local] 📡 Run data fetcher once
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python -m home_monitor fetch

fetcher-logs-local:  ## [local] 📋 View scheduled fetcher logs
	docker-compose --profile scheduled logs -f fetcher-scheduled

fetcher-once-bg:  ## [local] 🔄 Run fetcher once in background
	docker-compose --profile manual up -d fetcher

fetcher-once:  ## [local] 🔄 Run fetcher once in foreground
	docker-compose --profile manual up fetcher

fetcher-once-logs:  ## [local] 📋 View one-time fetcher logs
	docker-compose --profile manual logs -f fetcher

fetcher-start-local:  ## [local] ⏰ Start scheduled fetcher (every 5 minutes)
	docker-compose --profile scheduled up -d fetcher-scheduled

fetcher-stop-local:  ## [local] ⏹️  Stop scheduled fetcher
	docker-compose --profile scheduled stop fetcher-scheduled

flume-devices:  ## [local] 📱 List Flume devices for the authenticated user
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_flume_token.py --list-devices

flume-refresh:  ## [local] 🔄 Refresh Flume access token (usage: make flume-refresh REFRESH_TOKEN=your_token)
	@if [ -z "$(REFRESH_TOKEN)" ]; then \
		echo "❌ ERROR: REFRESH_TOKEN parameter is required"; \
		echo ""; \
		echo "Usage: make flume-refresh REFRESH_TOKEN=your_refresh_token"; \
		echo ""; \
		echo "Get your refresh token from the database (stored in location_api_configs.config)"; \
		echo "Or use the token_manager module to refresh automatically (happens automatically on API calls)"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_flume_token.py --refresh-token "$(REFRESH_TOKEN)"

flume-token:  ## [local] 🔐 Get Flume OAuth tokens using username/password (one-time setup)
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/get_flume_token.py

span-register:  ## [local] 🔐 Register with a Span panel (usage: make span-register HOST=192.168.1.200 [NAME="Main Panel"] [LOCATION=FOO])
	@if [ -z "$(HOST)" ]; then \
		echo "❌ ERROR: HOST is required"; \
		echo ""; \
		echo "Usage: make span-register HOST=192.168.1.200 [NAME=\"Main Panel\"] [LOCATION=FOO]"; \
		echo ""; \
		echo "Options:"; \
		echo "  HOST     - Panel IP address or hostname (required)"; \
		echo "  NAME     - Human-readable name for the panel (optional)"; \
		echo "  LOCATION - Site name to associate with (optional)"; \
		echo ""; \
		echo "Note: The panel must be unlocked (press door button 3x) before registering."; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_span_tokens.py register \
		--host "$(HOST)" $(if $(NAME),--name "$(NAME)") $(if $(LOCATION),--location "$(LOCATION)")

span-store:  ## [local] 🔑 Store an existing Span panel token (usage: make span-store HOST=ip TOKEN=token [NAME=name] [LOCATION=FOO])
	@if [ -z "$(HOST)" ] || [ -z "$(TOKEN)" ]; then \
		echo "❌ ERROR: HOST and TOKEN are required"; \
		echo ""; \
		echo "Usage: make span-store HOST=192.168.1.200 TOKEN=token [NAME=\"Main Panel\"] [LOCATION=FOO]"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_span_tokens.py store \
		--host "$(HOST)" --token "$(TOKEN)" $(if $(NAME),--name "$(NAME)") $(if $(LOCATION),--location "$(LOCATION)")

span-list:  ## [local] 📋 List all stored Span panel tokens
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_span_tokens.py list

span-test:  ## [local] 🧪 Test connectivity to a Span panel (usage: make span-test HOST=192.168.1.200)
	@if [ -z "$(HOST)" ]; then \
		echo "❌ ERROR: HOST is required"; \
		echo "Usage: make span-test HOST=192.168.1.200"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_span_tokens.py test --host "$(HOST)"

span-delete:  ## [local] 🗑️  Delete a Span panel token (usage: make span-delete SERIAL=xxx)
	@if [ -z "$(SERIAL)" ]; then \
		echo "❌ ERROR: SERIAL is required"; \
		echo "Usage: make span-delete SERIAL=<panel_serial>"; \
		exit 1; \
	fi
	@DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/manage_span_tokens.py delete --serial "$(SERIAL)"

span-register-remote:  ## [remote] 🔐 Register with a Span panel from remote (usage: make span-register-remote HOST=192.168.1.200 [NAME="Main Panel"] [LOCATION=FOO])
	@if [ -z "$(HOST)" ]; then \
		echo "❌ ERROR: HOST is required"; \
		echo ""; \
		echo "Usage: make span-register-remote HOST=192.168.1.200 [NAME=\"Main Panel\"] [LOCATION=FOO]"; \
		echo ""; \
		echo "Note: The panel must be unlocked (press door button 3x) before registering."; \
		echo "      Run this from remote when panels are only accessible from that network."; \
		exit 1; \
	fi
	@echo "🔐 Registering with Span panel at $(HOST) from $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_span_tokens.py register --host '$(HOST)' $(if $(NAME),--name '$(NAME)') $(if $(LOCATION),--location '$(LOCATION)')"

span-store-remote:  ## [remote] 🔑 Store an existing Span panel token (usage: make span-store-remote HOST=ip TOKEN=token [NAME=name] [LOCATION=FOO])
	@if [ -z "$(HOST)" ] || [ -z "$(TOKEN)" ]; then \
		echo "❌ ERROR: HOST and TOKEN are required"; \
		echo ""; \
		echo "Usage: make span-store-remote HOST=192.168.1.200 TOKEN=token [NAME=\"Main Panel\"] [LOCATION=FOO]"; \
		exit 1; \
	fi
	@echo "🔑 Storing Span panel token on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_span_tokens.py store --host '$(HOST)' --token '$(TOKEN)' $(if $(NAME),--name '$(NAME)') $(if $(LOCATION),--location '$(LOCATION)')"

span-list-remote:  ## [remote] 📋 List all stored Span panel tokens
	@echo "📋 Listing Span panel tokens on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_span_tokens.py list"

span-test-remote:  ## [remote] 🧪 Test connectivity to a Span panel from remote (usage: make span-test-remote HOST=192.168.1.200)
	@if [ -z "$(HOST)" ]; then \
		echo "❌ ERROR: HOST is required"; \
		echo "Usage: make span-test-remote HOST=192.168.1.200"; \
		exit 1; \
	fi
	@echo "🧪 Testing Span panel at $(HOST) from $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_span_tokens.py test --host '$(HOST)'"

span-delete-remote:  ## [remote] 🗑️  Delete a Span panel token (usage: make span-delete-remote SERIAL=xxx)
	@if [ -z "$(SERIAL)" ]; then \
		echo "❌ ERROR: SERIAL is required"; \
		echo "Usage: make span-delete-remote SERIAL=<panel_serial>"; \
		exit 1; \
	fi
	@echo "🗑️  Deleting Span panel token on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python scripts/manage_span_tokens.py delete --serial '$(SERIAL)'"

format:  ## [local] ✨ Format code with isort and black
	isort home_monitor/ scripts/
	black home_monitor/ scripts/

generate-dashboard-local:  ## [local] 📊 Generate Grafana dashboard JSON
	@PYTHONPATH=. python scripts/generate_dashboard.py
	@echo "✅ Dashboard generated. Refresh Grafana to see changes."

infra-down-local:  ## [local] ⬇️  Stop services
	docker-compose down

infra-logs-local:  ## [local] 📋 View docker-compose logs
	docker-compose logs -f

infra-up-local:  ## [local] ⬆️  Start services with docker-compose
	docker-compose up -d

init-db-local:  ## [local] 🗄️  Initialize database schema
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python -m home_monitor init-db

lint:  ## [local] 🔍 Check code style with isort and flake8
	isort --check-only home_monitor/ scripts/
	flake8 home_monitor/ scripts/

rachio-backfill:  ## [local] 🌧️  Backfill Rachio watering events (usage: make rachio-backfill START_DATE=2024-01-01 [END_DATE=2024-06-01] [SITE=FL])
	@if [ -z "$(START_DATE)" ]; then \
		echo "❌ ERROR: START_DATE is required"; \
		echo ""; \
		echo "Usage: make rachio-backfill START_DATE=2024-01-01 [END_DATE=2024-06-01] [SITE=FOO] [DRY_RUN=1]"; \
		echo ""; \
		echo "Options:"; \
		echo "  START_DATE  - Start date for backfill (YYYY-MM-DD format, required)"; \
		echo "  END_DATE    - End date for backfill (optional, defaults to now)"; \
		echo "  SITE        - Specific site to backfill (optional)"; \
		echo "  DRY_RUN     - Show what would be done without making changes (optional)"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make rachio-backfill START_DATE=2024-01-01"; \
		echo "  make rachio-backfill START_DATE=2024-01-01 END_DATE=2024-06-01"; \
		echo "  make rachio-backfill START_DATE=2024-01-01 SITE=FOO"; \
		echo "  make rachio-backfill START_DATE=2024-01-01 DRY_RUN=1"; \
		echo ""; \
		echo "Note: The Rachio API does not document how far back events can be fetched."; \
		echo "      The API has a rate limit of 3,500 requests per day."; \
		exit 1; \
	fi
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/rachio_backfill.py \
		--start-date "$(START_DATE)" \
		$(if $(END_DATE),--end-date "$(END_DATE)") \
		$(if $(SITE),--site "$(SITE)") \
		$(if $(DRY_RUN),--dry-run)

run: fetch  ## [local] ▶️  Alias for fetch

server:  ## [local] 🖥️  Run HTTP server
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python -m home_monitor server

setup-env:  ## [local] ⚙️  Copy example config files (.env and sites.json)
	@ENV_EXISTS=0; SITES_EXISTS=0; \
	if [ -f .env ]; then ENV_EXISTS=1; fi; \
	if [ -f sites.json ]; then SITES_EXISTS=1; fi; \
	if [ $$ENV_EXISTS -eq 1 ] && [ $$SITES_EXISTS -eq 1 ]; then \
		echo "⚠️  Both .env and sites.json already exist. Skipping to avoid overwriting."; \
		echo "   Delete them first if you want to recreate from examples."; \
		exit 1; \
	fi; \
	if [ $$ENV_EXISTS -eq 0 ]; then \
		cp env.example .env; \
		echo "✓ Created .env from env.example"; \
	else \
		echo "⚠️  .env already exists, skipping"; \
	fi; \
	if [ $$SITES_EXISTS -eq 0 ]; then \
		cp sites.example.json sites.json; \
		echo "✓ Created sites.json from sites.example.json"; \
	else \
		echo "⚠️  sites.json already exists, skipping"; \
	fi
	@echo ""
	@echo "⚠️  Please edit .env and sites.json with your configuration"

test:  ## [local] 🧪 Run unit tests
	pytest tests/ -v

test-format: format lint  ## [local] ✅ Format code and then lint (for CI)

test-service:  ## [local] 🧪 Test an API service (usage: make test-service SERVICE=tesla [LOCATION=name] [SAVE_TO_DB=1] [HIDE_RAW=1] ...)
	@if [ -z "$(SERVICE)" ]; then \
		echo "❌ ERROR: SERVICE is required"; \
		echo "Usage: make test-service SERVICE=<service> [LOCATION=name] [SAVE_TO_DB=1] [HIDE_RAW=1] ..."; \
		echo ""; \
		echo "Options:"; \
		echo "  SERVICE        - Service to test (required): tesla, enphase, openweather, tempest, flume, rachio, tankutility, or iaqualink"; \
		echo "  LOCATION       - Location name (optional, uses first available if not specified)"; \
		echo "  SAVE_TO_DB     - Save fetched data to database (optional, default: false)"; \
		echo "  HIDE_RAW       - Hide raw API response (optional, default: false)"; \
		echo "  LAT            - Latitude for testing without database (optional, OpenWeather only)"; \
		echo "  LON            - Longitude for testing without database (optional, OpenWeather only)"; \
		echo "  STATION_ID     - Station ID for testing without database (optional, Tempest only)"; \
		echo "  ENERGY_SITE_ID - Energy Site ID for testing without database (optional, Tesla only)"; \
		echo "  SYSTEM_ID      - System ID for testing without database (Enphase only)"; \
		echo "  DEVICE_ID      - Device ID for testing without database (Flume/Rachio only)"; \
		echo ""; \
		echo "Examples:"; \
		echo "  make test-service SERVICE=tesla"; \
		echo "  make test-service SERVICE=tesla ENERGY_SITE_ID=<energy_site_id>  # Test without database"; \
		echo "  make test-service SERVICE=enphase LOCATION=Home"; \
		echo "  make test-service SERVICE=enphase SYSTEM_ID=<system_id>  # Test without database"; \
		echo "  make test-service SERVICE=openweather SAVE_TO_DB=1"; \
		echo "  make test-service SERVICE=openweather LAT=37.7749 LON=-122.4194  # Test without database"; \
		echo "  make test-service SERVICE=tempest STATION_ID=35943  # Test without database"; \
		echo "  make test-service SERVICE=tempest HIDE_RAW=1"; \
		echo "  make test-service SERVICE=flume LOCATION=FL"; \
		echo "  make test-service SERVICE=flume DEVICE_ID=<device_id>  # Test without database"; \
		echo "  make test-service SERVICE=rachio LOCATION=FL"; \
		echo "  make test-service SERVICE=rachio DEVICE_ID=<device_id>  # Test without database"; \
		echo "  make test-service SERVICE=tankutility LOCATION=FL"; \
		echo "  make test-service SERVICE=tankutility DEVICE_ID=<device_id>  # Test without database"; \
		echo "  make test-service SERVICE=iaqualink  # Test iAqualink pool controller"; \
		echo "  make test-service SERVICE=iaqualink LOCATION=FL SAVE_TO_DB=1"; \
		exit 1; \
	fi
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. python scripts/test_service.py $(SERVICE) \
		$(if $(LOCATION),--location "$(LOCATION)") \
		$(if $(SAVE_TO_DB),--save-to-db) \
		$(if $(HIDE_RAW),--hide-raw) \
		$(if $(LAT),--lat $(LAT)) \
		$(if $(LON),--lon $(LON)) \
		$(if $(STATION_ID),--station-id $(STATION_ID)) \
		$(if $(ENERGY_SITE_ID),--energy-site-id $(ENERGY_SITE_ID)) \
		$(if $(SYSTEM_ID),--system-id $(SYSTEM_ID)) \
		$(if $(DEVICE_ID),--device-id $(DEVICE_ID))

api-local:  ## [local] 🌐 Start API server locally (without Docker)
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=. uvicorn home_monitor.api:app --host 0.0.0.0 --port 8000 --reload

api-up-local:  ## [local] 🌐 Start API server in Docker
	docker-compose up -d api

api-logs-local:  ## [local] 📋 View API server logs
	docker-compose logs -f api

api-stop-local:  ## [local] ⏹️  Stop API server
	docker-compose stop api

# =============================================================================
# Remote Commands
# =============================================================================
# Uses Docker Context to run commands on a remote host over SSH.
# Prerequisites:
#   1. SSH key-based auth to DEPLOY_HOST (ssh-copy-id if needed)
#   2. Docker installed on the remote host
#   3. One-time setup: make deploy-setup
#
# Configure DEPLOY_HOST at the top of this Makefile or override:
#   make deploy DEPLOY_HOST=user@mypi.local
#
# Note: Docker Compose resolves relative paths to absolute local paths.
# The remote host needs config files at $(DEPLOY_CONFIG_PATH).

db-migrate-to-remote:  ## [remote] 🔄 Migrate database from local to remote (dump + copy + restore)
	@echo "🔄 Migrating database from local to $(DEPLOY_HOST)..."
	$(MAKE) db-dump
	$(MAKE) db-restore-remote
	@echo ""
	@echo "✅ Database migration complete"

db-restore-remote:  ## [remote] 📥 Restore database dump to remote host (run db-dump first)
	@if [ ! -f /tmp/home_monitor_dump.sql ]; then \
		echo "❌ ERROR: /tmp/home_monitor_dump.sql not found"; \
		echo "Run 'make db-dump' first to create the dump file"; \
		exit 1; \
	fi
	@echo "📤 Copying dump to $(DEPLOY_HOST)..."
	scp /tmp/home_monitor_dump.sql $(DEPLOY_HOST):/tmp/
	@echo "📥 Restoring database on remote..."
	ssh $(DEPLOY_HOST) "cat /tmp/home_monitor_dump.sql | docker exec -i home-monitor-db psql -U home_monitor -d home_monitor"
	@echo ""
	@echo "✅ Database restore complete"

deploy-build-remote:  ## [remote] 🔨 Build images on remote host without starting containers
	@echo "Building on $(DEPLOY_HOST)..."
	$(REMOTE_COMPOSE) --profile manual --profile scheduled build"
	@echo ""
	@echo "✅ Build complete. Start with: make deploy-remote"

deploy-check-remote:  ## [remote] 🔍 Check remote Docker connection and running containers
	@echo "Checking connection to $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "docker info --format 'Docker version: {{.ServerVersion}}'" || \
		(echo "❌ Cannot connect via SSH." && exit 1)
	@echo ""
	@echo "Running containers:"
	@ssh $(DEPLOY_HOST) "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"

deploy-clean-remote:  ## [remote] 🧹 Remove Docker context for remote deployment
	@docker context rm $(DEPLOY_CONTEXT) 2>/dev/null && \
		echo "✅ Removed context '$(DEPLOY_CONTEXT)'" || \
		echo "⚠️  Context '$(DEPLOY_CONTEXT)' does not exist"

deploy-disable-https-remote:  ## [remote] 🔓 Disable HTTPS on Grafana and API (switch back to HTTP)
	@echo "🔓 Disabling HTTPS on remote services..."
	@sed -i '' 's/^GF_SERVER_PROTOCOL=.*/GF_SERVER_PROTOCOL=http/' .env 2>/dev/null || true
	@sed -i '' 's/^API_SSL_ENABLED=.*/API_SSL_ENABLED=false/' .env 2>/dev/null || true
	@echo "📤 Syncing .env to remote..."
	rsync -avz .env $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)/
	@echo "🔄 Recreating Grafana and API containers..."
	$(REMOTE_COMPOSE) up -d --force-recreate grafana api"
	@echo ""
	@echo "✅ HTTP restored!"
	@echo "   Grafana: http://$(shell echo $(DEPLOY_HOST) | cut -d@ -f2):3000"
	@echo "   API:     http://$(shell echo $(DEPLOY_HOST) | cut -d@ -f2):8000"

deploy-enable-https-remote:  ## [remote] 🔒 Enable HTTPS on Grafana and API (generates certs if needed) (usage: make deploy-enable-https-remote [CERT_HOSTNAME=hostname])
	@echo "🔒 Enabling HTTPS on remote services..."
	@ssh $(DEPLOY_HOST) "test -f $(DEPLOY_CONFIG_PATH)/certs/grafana.crt" || $(MAKE) deploy-generate-certs-remote $(if $(CERT_HOSTNAME),CERT_HOSTNAME=$(CERT_HOSTNAME))
	@echo "📝 Setting GF_SERVER_PROTOCOL=https in local .env..."
	@grep -q '^GF_SERVER_PROTOCOL=' .env 2>/dev/null && \
		sed -i '' 's/^GF_SERVER_PROTOCOL=.*/GF_SERVER_PROTOCOL=https/' .env || \
		echo 'GF_SERVER_PROTOCOL=https' >> .env
	@echo "📝 Setting API_SSL_ENABLED=true in local .env..."
	@grep -q '^API_SSL_ENABLED=' .env 2>/dev/null && \
		sed -i '' 's/^API_SSL_ENABLED=.*/API_SSL_ENABLED=true/' .env || \
		echo 'API_SSL_ENABLED=true' >> .env
	@echo "📤 Syncing .env to remote..."
	rsync -avz .env $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)/
	@echo "🔄 Recreating Grafana and API containers..."
	$(REMOTE_COMPOSE) up -d --force-recreate grafana api"
	@echo ""
	@echo "✅ HTTPS enabled!"
	@echo "   Grafana: https://$(shell echo $(DEPLOY_HOST) | cut -d@ -f2):3000"
	@echo "   API:     https://$(shell echo $(DEPLOY_HOST) | cut -d@ -f2):8000"
	@echo "   (You'll need to accept the self-signed certificate warning in your browser)"

deploy-exec-remote:  ## [remote] 🐚 Open shell in remote container (usage: make deploy-exec-remote SERVICE=postgres)
	@if [ -z "$(SERVICE)" ]; then \
		echo "Usage: make deploy-exec-remote SERVICE=<service>"; \
		echo ""; \
		echo "Available services: postgres, grafana, fetcher-scheduled"; \
		exit 1; \
	fi
	ssh -t $(DEPLOY_HOST) "docker exec -it home-monitor-$(SERVICE) sh"

deploy-fix-permissions-remote:  ## [remote] 🔧 Fix permissions on remote config directory (requires sudo)
	@echo "🔧 Fixing permissions on $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)..."
	@echo "   (You will be prompted for sudo password)"
	ssh -t $(DEPLOY_HOST) "sudo chown -R \$$USER:\$$USER $(DEPLOY_CONFIG_PATH)"
	@echo "✅ Permissions fixed"

deploy-generate-certs-remote:  ## [remote] 🔐 Generate self-signed TLS certificates for HTTPS (Grafana + API) (usage: make deploy-generate-certs-remote [CERT_HOSTNAME=hostname])
	$(eval CERT_CN := $(if $(CERT_HOSTNAME),$(CERT_HOSTNAME),$(shell echo $(DEPLOY_HOST) | cut -d@ -f2)))
	@echo "🔐 Generating self-signed certificates on $(DEPLOY_HOST) for hostname '$(CERT_CN)'..."
	ssh $(DEPLOY_HOST) "mkdir -p $(DEPLOY_CONFIG_PATH)/certs && \
		openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
		-keyout $(DEPLOY_CONFIG_PATH)/certs/grafana.key \
		-out $(DEPLOY_CONFIG_PATH)/certs/grafana.crt \
		-subj '/CN=$(CERT_CN)/O=HomeMonitor/C=US' \
		-addext 'subjectAltName=DNS:$(CERT_CN),DNS:localhost' && \
		chmod 644 $(DEPLOY_CONFIG_PATH)/certs/grafana.*"
	@echo "✅ Certificates generated at $(DEPLOY_CONFIG_PATH)/certs/"
	@echo "   CN/SAN: $(CERT_CN), localhost"

deploy-pull-remote:  ## [remote] ⬇️  Pull latest images on remote host (if using registry)
	$(REMOTE_COMPOSE) pull"

deploy:  ## [remote] 🚀 Full deploy: sync files, rebuild, and restart everything
	@echo "🚀 Deploying to $(DEPLOY_HOST)..."
	@echo ""
	@echo "📤 Step 1: Syncing config files..."
	@$(MAKE) deploy-sync-remote
	@echo ""
	@echo "🔨 Step 2: Rebuilding and restarting infrastructure..."
	$(REMOTE_COMPOSE) up -d postgres grafana"
	@echo ""
	@echo "🔨 Step 3: Rebuilding and restarting fetcher..."
	$(REMOTE_COMPOSE) --profile scheduled up -d --build fetcher-scheduled"
	@echo ""
	@echo "🔨 Step 4: Rebuilding and restarting API..."
	$(REMOTE_COMPOSE) up -d --build api"
	@echo ""
	@echo "✅ Deployment complete!"
	@echo ""
	@echo "View logs:"
	@echo "  make fetcher-logs-remote   # Fetcher logs"
	@echo "  make api-logs-remote       # API logs"
	@echo "  make infra-logs-remote     # All container logs"

deploy-remote:  ## [remote] 🚀 Deploy to remote host (builds and starts containers, no sync)
	@echo "Deploying to $(DEPLOY_HOST)..."
	$(REMOTE_COMPOSE) up -d --build"
	@echo ""
	@echo "✅ Deployment complete. View logs with: make infra-logs-remote"

deploy-setup:  ## [remote] 🔧 One-time setup: create Docker context for remote deployment
	@# Ensure SSH key is loaded in agent (avoids repeated passphrase prompts)
	@echo "🔑 Ensuring SSH key is loaded in agent..."
	@ssh-add -l >/dev/null 2>&1 || ssh-add 2>/dev/null || true
	@echo "🔍 Checking SSH connection to $(DEPLOY_HOST)..."
	@echo "   (You may be prompted for your SSH key passphrase once)"
	@ssh -o ConnectTimeout=10 -o AddKeysToAgent=yes $(DEPLOY_HOST) "echo 'SSH OK'" || \
		(echo "" && \
		 echo "❌ Cannot connect via SSH. Ensure:" && \
		 echo "   1. Host is reachable: ping $$(echo $(DEPLOY_HOST) | cut -d@ -f2)" && \
		 echo "   2. SSH key is set up: ssh-copy-id $(DEPLOY_HOST)" && \
		 exit 1)
	@echo ""
	@echo "🐳 Checking if Docker is installed on remote host..."
	@if ssh $(DEPLOY_HOST) "command -v docker" >/dev/null 2>&1; then \
		echo "✅ Docker is installed: $$(ssh $(DEPLOY_HOST) 'docker --version')"; \
	else \
		echo "⚠️  Docker is not installed on $(DEPLOY_HOST)"; \
		echo ""; \
		read -p "Would you like to install Docker now? [y/N] " answer; \
		if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
			echo ""; \
			echo "📦 Installing Docker on $(DEPLOY_HOST)..."; \
			echo "   (You may be prompted for the remote user's sudo password)"; \
			ssh -t $(DEPLOY_HOST) "curl -fsSL https://get.docker.com | sudo sh"; \
			echo ""; \
			echo "👤 Adding user to docker group (avoids needing sudo)..."; \
			ssh -t $(DEPLOY_HOST) "sudo usermod -aG docker \$$USER"; \
			echo ""; \
			echo "✅ Docker installed! The remote host needs to log out and back in"; \
			echo "   (or reboot) for group changes to take effect."; \
			echo ""; \
			echo "   After reconnecting, re-run: make deploy-setup"; \
			exit 0; \
		else \
			echo ""; \
			echo "Install Docker manually:"; \
			echo "  ssh -t $(DEPLOY_HOST) 'curl -fsSL https://get.docker.com | sudo sh'"; \
			echo "  ssh -t $(DEPLOY_HOST) 'sudo usermod -aG docker \$$USER'"; \
			echo ""; \
			echo "Then re-run: make deploy-setup"; \
			exit 1; \
		fi; \
	fi
	@echo ""
	@echo "🔧 Creating Docker context '$(DEPLOY_CONTEXT)'..."
	@docker context inspect $(DEPLOY_CONTEXT) >/dev/null 2>&1 && \
		echo "⚠️  Context '$(DEPLOY_CONTEXT)' already exists. Remove with: docker context rm $(DEPLOY_CONTEXT)" || \
		docker context create $(DEPLOY_CONTEXT) --docker "host=ssh://$(DEPLOY_HOST)"
	@echo ""
	@echo "🔗 Testing Docker connection..."
	@docker --context $(DEPLOY_CONTEXT) info --format '{{.Name}}' >/dev/null 2>&1 && \
		echo "✅ Successfully connected to remote Docker daemon" || \
		(echo "❌ Failed to connect to Docker daemon." && \
		 echo "   If Docker was just installed, log out/in on the Pi and retry." && \
		 exit 1)
	@echo ""
	@echo "📁 Creating config directory on remote host..."
	@echo "   (You may be prompted for sudo password)"
	@ssh -t $(DEPLOY_HOST) "sudo mkdir -p $(DEPLOY_CONFIG_PATH)/grafana/provisioning && sudo chown -R \$$USER:\$$USER $(DEPLOY_CONFIG_PATH)"
	@echo "✅ Config directory created at $(DEPLOY_CONFIG_PATH)"

deploy-sync-remote:  ## [remote] 📤 Sync project files to remote host for building
	@echo "📤 Syncing project to $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)..."
	@ssh $(DEPLOY_HOST) "mkdir -p $(DEPLOY_CONFIG_PATH)"
	rsync -avz --delete \
		--exclude '.git' \
		--exclude '__pycache__' \
		--exclude '*.pyc' \
		--exclude '.pytest_cache' \
		--exclude '.venv' \
		--exclude 'venv' \
		--exclude '.mypy_cache' \
		--exclude 'assets' \
		--exclude 'certs/*.crt' \
		--exclude 'certs/*.key' \
		./ $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)/
	@echo ""
	@echo "✅ Project sync complete"

drop-db-remote:  ## [remote] 🗑️  Drop all database tables
	@echo "🗑️  Dropping all database tables on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "docker exec home-monitor-db psql -U home_monitor -d home_monitor -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'"
	@echo ""
	@echo "✅ Database tables dropped on remote"

fetcher-logs-remote:  ## [remote] 📋 View fetcher logs
	ssh $(DEPLOY_HOST) "docker logs -f home-monitor-fetcher-scheduled"

api-up-remote:  ## [remote] 🌐 Start API server on remote
	@echo "🌐 Starting API server on $(DEPLOY_HOST)..."
	$(REMOTE_COMPOSE) up -d api"

api-logs-remote:  ## [remote] 📋 View API server logs on remote
	ssh $(DEPLOY_HOST) "docker logs -f home-monitor-api"

api-stop-remote:  ## [remote] ⏹️  Stop API server on remote
	@echo "⏹️  Stopping API server on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "docker stop home-monitor-api || true"

fetcher-start-remote:  ## [remote] ⏰ Start scheduled fetcher
	@echo "⏰ Starting scheduled fetcher on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile scheduled up -d fetcher-scheduled"

fetcher-stop-remote:  ## [remote] ⏹️  Stop scheduled fetcher
	@echo "⏹️  Stopping scheduled fetcher on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "docker stop home-monitor-fetcher-scheduled || true"

generate-dashboard-remote:  ## [remote] 📊 Generate and sync Grafana dashboard to remote
	@PYTHONPATH=. python scripts/generate_dashboard.py
	@echo "📤 Syncing dashboard to $(DEPLOY_HOST)..."
	rsync -avz grafana/provisioning/dashboards/ $(DEPLOY_HOST):$(DEPLOY_CONFIG_PATH)/grafana/provisioning/dashboards/
	@echo "✅ Dashboard synced. Refresh Grafana on remote to see changes."

infra-down-remote:  ## [remote] ⬇️  Stop containers
	$(REMOTE_COMPOSE) down"

infra-logs-remote:  ## [remote] 📋 View logs from containers
	$(REMOTE_COMPOSE) logs -f"

infra-ps-remote:  ## [remote] 📊 Show status of containers
	$(REMOTE_COMPOSE) ps"

infra-restart-remote:  ## [remote] 🔄 Restart containers (no rebuild)
	$(REMOTE_COMPOSE) restart"

infra-up-remote:  ## [remote] 🏗️  Start only postgres and grafana (no fetcher)
	@echo "Starting infrastructure on $(DEPLOY_HOST)..."
	$(REMOTE_COMPOSE) up -d postgres grafana"
	@echo ""
	@echo "✅ Infrastructure started. Postgres and Grafana are running."

init-db-remote:  ## [remote] 🗄️  Initialize database schema
	@echo "🗄️  Initializing database schema on $(DEPLOY_HOST)..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python -m home_monitor init-db"
	@echo ""
	@echo "✅ Database initialized on remote"

redeploy-fetcher-remote:  ## [remote] 🔄 Init DB, rebuild fetcher, and restart scheduled fetcher
	@echo "🔄 Redeploying fetcher on $(DEPLOY_HOST)..."
	@echo ""
	@echo "📦 Step 1/3: Initializing database schema..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile manual run --rm fetcher \
		python -m home_monitor init-db"
	@echo ""
	@echo "🔨 Step 2/3: Building fetcher image..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile scheduled build fetcher-scheduled"
	@echo ""
	@echo "🚀 Step 3/3: Restarting scheduled fetcher..."
	ssh $(DEPLOY_HOST) "cd $(DEPLOY_CONFIG_PATH) && docker compose -f docker-compose.prod.yml --profile scheduled up -d --force-recreate fetcher-scheduled"
	@echo ""
	@echo "✅ Fetcher redeployed! View logs with: make fetcher-logs-remote"

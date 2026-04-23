#!/bin/bash
# Convenience script for running Traktor with Docker

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Traktor Docker Runner"
echo "======================="
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found!${NC}"
    echo "Please create a .env file with your Plex credentials."
    echo "See README.md for details."
    exit 1
fi

# Create data directories if they don't exist
mkdir -p data/logs data/config

# Parse command line arguments
ARGS=""
COMPOSE_CMD="docker-compose"

# Check if docker compose (new) or docker-compose (old)
if ! command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker compose"
fi

case "${1:-}" in
    build)
        echo "Building Docker image..."
        $COMPOSE_CMD build
        ;;
    run)
        shift
        echo "Running Traktor..."
        $COMPOSE_CMD run --rm traktor "$@"
        ;;
    verbose)
        echo "Running with verbose output..."
        $COMPOSE_CMD run --rm traktor -v
        ;;
    auth)
        echo "Forcing Trakt re-authentication..."
        $COMPOSE_CMD run --rm traktor --force-auth
        ;;
    logs)
        echo "Showing logs..."
        tail -f data/logs/traktor.log
        ;;
    shell)
        echo "Opening shell in container..."
        $COMPOSE_CMD run --rm --entrypoint /bin/bash traktor
        ;;
    stop)
        echo "Stopping containers..."
        $COMPOSE_CMD down
        ;;
    clean)
        echo "Cleaning up Docker resources..."
        $COMPOSE_CMD down
        docker rmi traktor-traktor 2>/dev/null || true
        echo -e "${YELLOW}Note: data/ directory was not removed.${NC}"
        ;;
    *)
        echo "Usage: ./docker-run.sh [command]"
        echo ""
        echo "Commands:"
        echo "  build     - Build the Docker image"
        echo "  run       - Run the sync (pass additional args after 'run')"
        echo "  verbose   - Run with verbose output"
        echo "  auth      - Force Trakt re-authentication"
        echo "  logs      - View logs"
        echo "  shell     - Open shell in container"
        echo "  stop      - Stop running containers"
        echo "  clean     - Remove containers and images"
        echo ""
        echo "Examples:"
        echo "  ./docker-run.sh build"
        echo "  ./docker-run.sh run"
        echo "  ./docker-run.sh run -v"
        echo "  ./docker-run.sh logs"
        ;;
esac

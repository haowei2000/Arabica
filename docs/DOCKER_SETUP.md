# Docker Compose Setup Guide

This guide explains how to set up and run the Structure service using Docker Compose with all necessary components.

## Overview

The Docker Compose setup includes the following services:
- `structure-app`: Main FastAPI application
- `postgres`: PostgreSQL database for primary data storage
- `mysql`: MySQL database for additional data storage
- `redis`: Redis cache and session storage
- `ollama`: Local LLM service
- `redis-commander`: Redis management UI (optional)

## Prerequisites

- Docker Engine (version 20.10 or higher)
- Docker Compose (version 2.0 or higher)
- At least 8GB of RAM (recommended 16GB for optimal performance with Ollama)

## Setup Instructions

### 1. Environment Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file to customize your configuration:
   ```bash
   nano .env
   ```

   Pay special attention to:
   - `AUTH__JWT_SECRET_KEY`: Change this to a strong, random secret key
   - `POSTGRES_PASSWORD`: Set a secure PostgreSQL password
   - `MYSQL_PASSWORD`: Set a secure MySQL password
   - `REDIS_PASSWORD`: Set a secure Redis password
   - `DASHSCOPE_API_KEY`: If using DashScope AI services

### 2. Initialize Databases (Optional)

If you want to run custom initialization scripts:

1. Create a `init-postgres.sql` file for PostgreSQL initialization
2. Create a `init-mysql.sql` file for MySQL initialization

### 3. Start the Services

Start all services in the background:
```bash
docker-compose up -d
```

To see the logs in real-time:
```bash
docker-compose logs -f
```

### 4. Check Service Status

Check if all services are running properly:
```bash
docker-compose ps
```

### 5. Access the Services

- **Main Application**: `http://localhost:8000`
- **MCP Service**: `http://localhost:9000`
- **PostgreSQL**: `localhost:5432` (internal: `postgres:5432`)
- **MySQL**: `localhost:3306` (internal: `mysql:3306`)
- **Redis**: `localhost:6379` (internal: `redis:6379`)
- **Ollama**: `http://localhost:11434`
- **Redis Commander**: `http://localhost:8081`

## Management Commands

### View Logs
```bash
# View all services logs
docker-compose logs

# View specific service logs
docker-compose logs structure-app

# Follow logs in real-time
docker-compose logs -f structure-app
```

### Execute Commands in Containers
```bash
# Execute a command in the main app container
docker-compose exec structure-app bash

# Execute database migrations (if applicable)
docker-compose exec structure-app python -m alembic upgrade head
```

### Stop Services
```bash
# Stop all services
docker-compose down

# Stop and remove volumes (WARNING: This will delete all data)
docker-compose down -v
```

### Scale Services
```bash
# Scale the main application to 2 instances
docker-compose up -d --scale structure-app=2
```

## Configuration Details

### Services Configuration

- **structure-app**: Built from the Dockerfile in the `docker/` directory, with health checks and resource limits
- **postgres**: PostgreSQL 15 with health checks and persistent volume
- **mysql**: MySQL 8.0 with health checks and persistent volume
- **redis**: Redis 7 with authentication and persistence
- **ollama**: Latest Ollama image with resource configuration for AI models
- **redis-commander**: Web UI for Redis management

### Environment Variables

The service uses environment variables from the `.env` file for configuration. These include:

- Port configurations for all services
- Database connection parameters
- Authentication settings
- Resource limits
- API keys and secrets

## Resource Considerations

- The setup includes resource limits to prevent excessive usage
- Ollama requires significant memory when running large language models
- Adjust `MEMORY_LIMIT` in the `.env` file based on your system capabilities

## Troubleshooting

### Common Issues

1. **Port already in use**: Check if services are already running on the configured ports
2. **Database initialization fails**: Ensure that the database containers have enough time to initialize
3. **Health checks failing**: Check logs to see if services are starting properly
4. **Ollama performance**: If using Ollama, ensure you have enough memory allocated

### Useful Commands for Troubleshooting

```bash
# Check Docker system resources
docker stats

# Check disk space usage
docker system df

# Remove unused containers, networks, images
docker system prune

# View resource usage
docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.Status}}"
```

## Production Considerations

- Use strong passwords and secret keys in production
- Configure SSL/TLS termination with a reverse proxy (like nginx)
- Regularly backup database volumes
- Monitor resource usage and scale accordingly
- Consider using a more secure authentication mechanism
- Implement proper logging aggregation for production monitoring
# Agent Worker Setup and Operation

## Overview

The agent worker is responsible for processing queued agent tasks asynchronously. It listens for tasks on Redis channels and executes them in the background, streaming results back to clients via Server-Sent Events (SSE).

## Starting the Worker

### Development Environment

#### Method 1: Using the Shell Script (Recommended)
```bash
# Navigate to the project root
cd /path/to/ai630

# Make the script executable (if not already done)
chmod +x scripts/start_worker.sh

# Start the worker
./scripts/start_worker.sh
```

#### Method 2: Direct Python Execution
```bash
# Navigate to the project root
cd /path/to/ai630

# Activate virtual environment
source .venv/bin/activate

# Start the worker (new method)
python -m src.structure.workers

# Or the old method (still works)
python -m src.structure.workers.start_worker
```

### Production Environment

#### Using systemd (Linux)

1. Copy the service file to the systemd directory:
```bash
sudo cp deploy/agent-worker.service /etc/systemd/system/
```

2. Edit the service file to match your installation path:
```bash
sudo nano /etc/systemd/system/agent-worker.service
```

3. Reload systemd and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable agent-worker
sudo systemctl start agent-worker
```

4. Check the service status:
```bash
sudo systemctl status agent-worker
```

#### Using Docker (Alternative)

You can also run the worker in a Docker container. Create a `docker-compose.yml` service:

```yaml
version: '3.8'
services:
  agent-worker:
    build: .
    command: python -m src.structure.workers.start_worker
    environment:
      - ENV_FILE=.env
    volumes:
      - .:/app
    depends_on:
      - redis
      - postgres
```

## Worker Configuration

The worker uses the same configuration as the main application:

- **Database connections**: Configured via environment variables
- **Redis connection**: Configured via environment variables
- **Logging**: Uses the application's logging configuration

## Monitoring and Maintenance

### Logs

View worker logs:

```bash
# For systemd service
sudo journalctl -u agent-worker -f

# For direct execution
tail -f /var/log/structure/worker.log
```

### Scaling

You can run multiple worker instances to process tasks in parallel:

```bash
# Start multiple workers
./scripts/start_worker.sh &  # Worker 1
./scripts/start_worker.sh &  # Worker 2
./scripts/start_worker.sh &  # Worker 3
```

Each worker will consume tasks from the same Redis queue, providing automatic load balancing.

## Troubleshooting

### Common Issues

1. **Database Connection Failed**
   - Check that the database is running
   - Verify database connection settings in `.env` file
   - Ensure the worker has network access to the database

2. **Redis Connection Failed**
   - Check that Redis is running
   - Verify Redis connection settings in `.env` file
   - Ensure the worker has network access to Redis

3. **Module Import Errors**
   - Ensure you're running the script from the project root
   - Verify that the virtual environment is activated
   - Check that all dependencies are installed

### Graceful Shutdown

The worker handles SIGINT and SIGTERM signals gracefully:
- Press Ctrl+C to stop the worker
- Send SIGTERM to stop the worker gracefully
- The worker will finish processing the current task before shutting down

## Architecture Notes

The worker follows this processing flow:

1. Subscribes to the `agent_tasks` Redis channel
2. Receives task messages with task_id, agent_id, and payload
3. Updates task status to "running" in the database
4. Executes the agent and streams output via Redis pub/sub
5. Updates task status to "success" or "failed" in the database
6. Publishes final result to the task-specific channel

This architecture allows for:
- Asynchronous task processing
- Real-time streaming of results to clients
- Persistent task tracking
- Scalable worker deployment
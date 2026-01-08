# Python PostgreSQL Backend

## Overview
A Python Flask backend API with PostgreSQL database integration.

## Project Structure
- `app.py` - Main Flask application with REST API endpoints
- `pyproject.toml` - Python dependencies

## Tech Stack
- **Backend**: Python 3.11, Flask
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Port**: 5000

## API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info and available endpoints |
| GET | `/items` | List all items |
| POST | `/items` | Create a new item |
| GET | `/items/<id>` | Get item by ID |
| PUT | `/items/<id>` | Update an item |
| DELETE | `/items/<id>` | Delete an item |
| GET | `/health` | Health check with DB status |

## Database
Uses PostgreSQL via the `DATABASE_URL` environment variable. The `items` table is created automatically on startup.

## Running
```bash
python app.py
```

## Recent Changes
- 2026-01-08: Initial setup with Flask, SQLAlchemy, and CRUD API for items

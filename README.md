---

# BRFN Marketplace

Django 6 + MySQL 8
Fully containerised using Docker Compose.

---

## Requirements

* Docker Desktop installed
  (Includes Docker + Docker Compose)

Nothing else is required.
No Python, no MySQL, no virtual environment.

---

## Run the Project Locally

### 1. Clone the repository

```bash
git clone <REPOSITORY_URL>
cd brfn_marketplace
```

---

### 2. Create environment file

```bash
cp .env.example .env
```

No edits are required for local development.

---

### 3. Start the containers

```bash
docker compose up --build
```

Wait until you see:

```
Starting development server at http://0.0.0.0:8000/
```

Ignore `0.0.0.0`. Use `localhost` instead.

---

### 4. Apply database migrations (new terminal)

```bash
docker compose exec web python manage.py migrate
```

---

### 5. Create an admin user (optional)

```bash
docker compose exec web python manage.py createsuperuser
```

---

## Access the Application

* Main site:
  [http://localhost:8000](http://localhost:8000)

* Products page:
  [http://localhost:8000/products/](http://localhost:8000/products/)

* Admin panel:
  [http://localhost:8000/admin/](http://localhost:8000/admin/)

---

## Stop the Project

Press:

```
CTRL + C
```

Then run:

```bash
docker compose down
```

---

## Reset the Database (if needed)

⚠ This deletes all data.

```bash
docker compose down -v
docker compose up --build
docker compose exec web python manage.py migrate
```

---

That’s it.

---

Once you paste this in:

```bash
git add README.md
git commit -m "Simplified README with clear local setup instructions"
git push
```
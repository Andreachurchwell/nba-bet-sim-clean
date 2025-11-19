<div align="center"> <img src="assets/she_bets_logo.png" alt="SheBETS Logo" width="220"/>
🏀 SheBETS — NBA Betting Simulator (MVP)

Pick winners. Practice analytics. Fake bankroll — real learning.

Built with FastAPI (backend) + Streamlit (frontend)

</div>
📋 Overview

SheBETS is a class project that simulates NBA betting using live game data from the balldontlie API.

Users can:

View today’s games

Place fake bets

Track their virtual bankroll

Settle bets based on real results

Export data

See their game history and stats

This project reinforces full-stack concepts from the JTC AISE program:

REST API design with FastAPI

Modular architecture (api, services, core)

Clean URL routing

CSV/JSON persistence (wallet + bet ledger)

Streamlit UI design and event-driven programming

Using real-world API data

Logging, error handling, and request flow

⚙️ Features
✔ Functional Features

Browse today’s and recent NBA games

Place bets with a virtual bankroll

Settle bets after final scores update

Auto-updating wallet on every win/loss

CSV exports for:

Bet ledger

Game results

Schedules

Local persistence using CSV + JSON

✔ UX & UI Enhancements

Clean, custom Streamlit theme

Soft pink SheBETS branding

Organized tabs (Games, Bets, Wallet, Results)

Form-based UI for consistent inputs

✔ Backend Architecture

FastAPI with modular structure:

api/ → Routers (/games, /bets, /wallet)

services/ → External API client (balldontlie)

core/ → Config, utils, constants

data/ → Local CSV + wallet store

Separation of:

Business logic

Data access

API transport

🧩 Project Structure
shebets/
│
├── backend/                   # FastAPI backend
│   ├── api/
│   │   ├── routers/
│   │   │   ├── bets.py
│   │   │   ├── games.py
│   │   │   └── wallet.py
│   │   └── main.py            # FastAPI entrypoint
│   ├── services/              # Balldontlie API client + helpers
│   ├── core/                  # Settings, config, utils
│   └── data/                  # CSV bet ledger + JSON wallet
│
├── streamlit/                 # Frontend assets
│   ├── streamlit_style.css
│   └── components/ (optional)
│
├── streamlit_app.py           # Streamlit UI
├── requirements.txt
├── assets/
│   └── courtcash_logo.svg     # Temporary placeholder logo
└── README.md

🧠 Tech Stack
Layer	Technology
Frontend	Streamlit
Backend	FastAPI
API Data	balldontlie NBA API
Persistence	CSV (bets), JSON (wallet)
Language	Python 3.12
UX	Custom streamlit CSS + soft-pink theme
🚀 Running Locally
1️⃣ Clone the repo
git clone https://github.com/Andreachurchwell/shebets.git
cd shebets

2️⃣ Create & activate venv
python -m venv venv
venv\Scripts\activate        # Windows
# OR
source venv/bin/activate     # Mac/Linux

3️⃣ Install dependencies
pip install -r requirements.txt

4️⃣ Start the FastAPI backend
cd backend
uvicorn api.main:app --reload


(Updated to match your actual working command as of today.)

5️⃣ Start the Streamlit frontend (new terminal)
streamlit run streamlit_app.py

🧰 Future Enhancements
🔧 Technical

Move from CSV/JSON → SQLite or PostgreSQL

Real user accounts with authentication

Background job to auto-settle bets

Full service separation (wallet service, bet service, game service)

🔮 Features

Player props

Team logos + standings screen

Analytics dashboard (win rate, bet history, trends)

Real odds integration (TheOddsAPI or similar)

👩‍💻 Author

Andrea Churchwell
Justice Through Code — AISE 2026 Cohort
📍 Selmer, Tennessee
💬 "From dental assistant to applied AI engineer — one project at a time."

<div align="center"> <sub>© 2025 SheBETS — For learning and fun. Not for real gambling. 🏀</sub> </div>
<div align="center">
  <img src="assets/courtcash_logo.svg" alt="CourtCash Logo" width="220"/>

  # 🏀 CourtCash — NBA Betting Simulator (MVP)
  **Practice picking winners. Fake bankroll. Real learning.**

  Built with **FastAPI** (backend) + **Streamlit** (frontend)
</div>

---

## 📋 Overview

**CourtCash** is a class project that simulates NBA game betting using **real API data**.  
You can browse current and past games, place virtual bets, and track your fake bankroll — all through a sleek Streamlit interface connected to a FastAPI backend.

This project was built as a hands-on way to reinforce full-stack Python concepts covered in class over the past six weeks:
- REST APIs with FastAPI  
- Data persistence with CSV/JSON  
- Streamlit UI design and caching  
- Modular backend structure (`core`, `services`, `routers`)  
- Practical, applied Python development

---

## ⚙️ Features

✅ View NBA game schedules and results  
✅ Place fake bets and track balance  
✅ Wallet auto-updates after each bet  
✅ Settle bets once games finish  
✅ Export results/schedules to CSV  
✅ Clean dark/light Streamlit theme  
✅ Organized modular FastAPI backend  

---

## 🧩 Project Structure

nba-bet-sim-clean/
│
├── backend/ # FastAPI backend (API + logic)
│ ├── api/routers/ # Routes for /games and /bets
│ ├── core/ # Config + setup
│ ├── services/ # API client for NBA data
│ └── ...
│
├── streamlit/ # Streamlit frontend
│ └── streamlit_style.css # Custom theme styling
│
├── assets/ # Logos, icons, visuals
│ └── courtcash_logo.svg
│
├── data/ # Local CSV and wallet data
│
├── streamlit_app.py # Main Streamlit entry point
├── requirements.txt # Dependencies
├── .gitignore
└── README.md



## 🧠 Tech Stack

| Layer | Technology |
|-------|-------------|
| **Frontend** | Streamlit |
| **Backend** | FastAPI |
| **Data** | NBA public API + CSV/JSON |
| **Language** | Python 3.12 |
| **UI Styling** | Custom CSS (Streamlit theme) |

---

## 🚀 Running Locally

### 1️⃣ Clone the repo
```bash
git clone https://github.com/Andreachurchwell/nba-bet-sim-clean.git
cd nba-bet-sim-clean

2️⃣ Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
# OR
source venv/bin/activate  # Mac/Linux

3️⃣ Install dependencies
pip install -r requirements.txt

4️⃣ Start the FastAPI backend
cd backend
uvicorn main:app --reload

5️⃣ Start the Streamlit frontend (in a new terminal)
streamlit run streamlit_app.py

```
---
🧰 Future Enhancements

 Player prop bets

 Real-time odds integration

 SQLite or Postgres backend

 Team logos and standings view

 User authentication

 ---
 👩‍💻 Author

Andrea Churchwell
Justice Through Code | AISE 2026 Cohort
📍 Selmer, Tennessee
💬 “From dental assistant to full-stack Python developer.”

<div align="center"> <sub>© 2025 CourtCash — Built for learning, not gambling. 🏀</sub> </div> 
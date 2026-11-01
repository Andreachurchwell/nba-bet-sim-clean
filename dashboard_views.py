"""Pure presentation calculations shared with read-only dashboard tests."""

import pandas as pd


def performance_frames(rows: list[dict], starting_bankroll: float):
    """Preserve the legacy placement-date reconstruction, scoped to one ledger."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, pd.DataFrame()
    frame["stake"] = pd.to_numeric(frame["stake"], errors="coerce").fillna(0.0)
    frame["payout"] = pd.to_numeric(frame["payout"], errors="coerce").fillna(0.0)
    frame["placed_at"] = pd.to_datetime(frame["placed_at"], errors="coerce")
    frame["status"] = frame["status"].astype(str).str.lower()
    frame["net"] = 0.0
    settled = frame["status"].isin(["won", "lost"])
    frame.loc[settled, "net"] = frame.loc[settled, "payout"] - frame.loc[settled, "stake"]
    frame["cash_flow"] = -frame["stake"]
    won = frame["status"] == "won"
    frame.loc[won, "cash_flow"] += frame.loc[won, "payout"]
    # Only Phase 5 ledgers have cancellation refunds. The archived calculation
    # remains exactly the original won/lost/open placement-date reconstruction.
    if "idempotency_key" in frame.columns:
        voided = frame["status"] == "void"
        frame.loc[voided, "cash_flow"] += frame.loc[voided, "payout"]
    frame = frame.sort_values("placed_at").copy()
    frame["bankroll"] = starting_bankroll + frame["cash_flow"].cumsum()
    daily = frame.dropna(subset=["placed_at"]).copy()
    daily["day"] = daily["placed_at"].dt.date
    daily = daily.groupby("day", as_index=False)["cash_flow"].sum()
    daily["result"] = daily["cash_flow"].apply(lambda value: "Inflow" if value >= 0 else "Outflow")
    return frame, daily

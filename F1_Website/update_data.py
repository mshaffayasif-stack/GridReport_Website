import os
import json
import datetime
import fastf1
import pandas as pd

CACHE_DIR = "fastf1_cache"
OUT_DIR = "data"
OUT_FILE = os.path.join(OUT_DIR, "season_26.json")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

def get_schedule(year):
    schedule = fastf1.get_event_schedule(year)
    schedule = schedule[schedule["EventFormat"] != "testing"]

    now_utc = pd.Timestamp.now(tz="UTC")
    completed = schedule[pd.to_datetime(schedule["Session5Date"], utc=True) < now_utc]
    return completed


def extract_data(year, round_num):
    session = fastf1.get_session(year, round_num, 'R')
    session.load(laps=True, telemetry=True, weather=False)
    results = session.results
    laps = session.laps

    classification = []
    for _, row in results.iterrows():
        grid = int(row["GridPosition"]) if pd.notnull(row["GridPosition"]) and row["GridPosition"] > 0 else 0
        finish = int(row["Position"]) if pd.notnull(row["Position"]) and row["Position"] > 0 else 0
        points = float(row["Points"]) if pd.notnull(row["Points"]) else 0.0

        classification.append({
            "driver": str(row["Abbreviation"]),
            "driver_number": str(row["DriverNumber"]),
            "team": str(row["TeamName"]),
            "grid": grid,
            "finish": finish,
            "net_gain": (grid - finish) if (grid > 0 and finish > 0) else 0,
            "points": points,
            "status": str(row["Status"])
        })

    has_sprint = False
    for i in range(1, 6):
        if str(session.event.get(f"Session{i}", "")).lower() == "sprint":
            has_sprint = True
            break
            
    if has_sprint:
        print(f"  -> [SPRINT DETECTED] Fetching sprint points for Round {round_num}...")
        try:
            # Use 'Sprint' instead of 'S' to ensure FastF1 finds the right session
            sprint_session = fastf1.get_session(year, round_num, "Sprint")
            sprint_session.load(telemetry=False, weather=False)
            
            for _, s_row in sprint_session.results.iterrows():
                s_driver = str(s_row["Abbreviation"])
                s_points = float(s_row["Points"]) if pd.notnull(s_row["Points"]) else 0.0
                
                if s_points > 0:
                    for entry in classification:
                        if entry["driver"] == s_driver:
                            entry["points"] += s_points
                            # Print to terminal so we can prove it's working
                            print(f"     + Added {s_points} Sprint points to {s_driver}")
                            break
        except Exception as e:
            print(f"  [Error] Failed to load Sprint points for round {round_num}: {e}")

    stints = laps.groupby(["Driver", "Stint"])["Compound"].first().reset_index()
    tyre_strategies = {}
    for driver, group in stints.groupby("Driver"):
        compounds = [str(c) for c in group["Compound"].tolist() if pd.notnull(c)]
        tyre_strategies[str(driver)] = compounds

    try:
        fastest_lap = laps.pick_fastest()
        pos_data = fastest_lap.get_pos_data()
        downsampled = pos_data.iloc[::15]
        track_coordinates = {
            "x": [int(x) for x in downsampled["X"].tolist() if pd.notnull(x)],
            "y": [int(y) for y in downsampled["Y"].tolist() if pd.notnull(y)]
        }
    except Exception as e:
        print(f"  [Warning] Could not extract track coordinates for round {round_num}: {e}")
        track_coordinates = {"x": [], "y": []}

    return {
        "round": int(round_num),
        "event_name": str(session.event["EventName"]),
        "location": str(session.event["Location"]),
        "country": str(session.event["Country"]),
        "date": str(session.event["Session5Date"]).split(" ")[0],
        "track_coordinates": track_coordinates,
        "classification": classification,
        "tyre_strategies": tyre_strategies
    }


def calculate_standings(races):
    driver_points = {}
    driver_teams = {}
    driver_wins = {}
    constructor_points = {}
    constructor_wins = {}

    for race in races:
        for entry in race["classification"]:
            driver = entry["driver"]
            team = entry["team"]
            pts = entry["points"]
            is_win = 1 if entry["finish"] == 1 else 0

            driver_points[driver] = driver_points.get(driver, 0.0) + pts
            driver_wins[driver] = driver_wins.get(driver, 0) + is_win
            driver_teams[driver] = team

            if team and team != "nan":
                constructor_points[team] = constructor_points.get(team, 0.0) + pts
                constructor_wins[team] = constructor_wins.get(team, 0) + is_win

    driver_standings = []
    for driver, points in driver_points.items():
        driver_standings.append({
            "driver": driver,
            "team": driver_teams.get(driver, "Unknown"),
            "points": round(points, 1),
            "wins": driver_wins.get(driver, 0)
        })

    driver_standings.sort(key=lambda x: (x["points"], x["wins"]), reverse=True)
    for index, row in enumerate(driver_standings, start=1):
        row["position"] = index

    constructor_standings = []
    for team, points in constructor_points.items():
        constructor_standings.append({
            "team": team,
            "points": round(points, 1),
            "wins": constructor_wins.get(team, 0)
        })
    constructor_standings.sort(key=lambda x: (x["points"], x["wins"]), reverse=True)
    for index, row in enumerate(constructor_standings, start=1):
        row["position"] = index

    return{
        "drivers": driver_standings,
        "constructors": constructor_standings
    }

def build_dataset(year):
    schedule = get_schedule(year)

    if schedule.empty:
        print("No completed races for this season")
        return

    races_data = []
    for _, event in schedule.iterrows():
        round_num = int(event["RoundNumber"])
        try:
            race_payload = extract_data(year, round_num)
            races_data.append(race_payload)
        except Exception as err:
            print(f"[Error] Failed to process Round{round_num}: {err}")


    standings = calculate_standings(races_data)

    master_payload = {
        "season": year,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_races_completed": len(races_data),
        "championship_standings": standings,
        "races": races_data
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(master_payload, f, indent=2)


if __name__ == "__main__":
    current_year = datetime.datetime.now(datetime.timezone.utc).year
    build_dataset(current_year)
                

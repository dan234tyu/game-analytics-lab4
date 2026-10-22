"""Генерация синтетических логов соревновательного шутера 5v5 со «скрытыми аномалиями».

Таблицы:
  data/matches.csv       — сводка матчей (match_id, map_id, duration_sec, server_region, winning_team, queue_time_sec, match_date)
  data/player_stats.csv  — статистика игроков в матче (match_id, player_id, team_side, hero_id, weapon_id,
                           kills, deaths, assists, mmr_before, mmr_after, ping)
  data/reports.csv       — жалобы (match_id, reporter_id, reported_id, reason)
  data/ground_truth.csv  — скрытая «правда» о профилях игроков (только для валидации алгоритмов)

Заложенные аномалии:
  * ~20% матчей собраны с багом балансировщика: сильные пятеро против слабых (большая ΔMMR);
  * карта De_Inferno перекошена в пользу Защиты, De_Nuke — слегка в пользу Атаки;
  * 15 читеров (аномальный K/D, много жалоб на cheating), 25 смурфов (низкий MMR, высокий навык),
    40 токсичных игроков (жалобы на toxicity);
  * Hero_1 — популярная «имба», Hero_2 — популярный, но слабый, Hero_3 — сильный ситуативный пик.

Запуск из корня проекта:  python src/generate_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 2026
N_PLAYERS = 2000
N_MATCHES = 3000
DATA = Path(__file__).resolve().parent.parent / "data"

MAPS = ["De_Dust2", "De_Inferno", "De_Mirage", "De_Nuke"]
MAP_P = [0.35, 0.25, 0.25, 0.15]
# Сдвиг силы в пользу Атаки (в пунктах Elo): Inferno — сильный перекос к Защите, Nuke — лёгкий к Атаке
MAP_SIDE_BIAS = {"De_Dust2": 0, "De_Inferno": -110, "De_Mirage": 0, "De_Nuke": 25}
REGIONS = ["EU", "NA", "ASIA", "RU"]
REGION_PING = {"EU": 35, "NA": 45, "ASIA": 60, "RU": 40}
WEAPONS = ["Rifle_AK", "Rifle_M4", "Sniper_AWP", "SMG_MP9", "Pistol_Deagle", "Shotgun_Nova"]

N_HEROES = 20
HEROES = [f"Hero_{i}" for i in range(1, N_HEROES + 1)]
# Сила героя (вклад в силу команды, Elo) и популярность (веса выбора)
HERO_STRENGTH = {h: 0.0 for h in HEROES}
HERO_STRENGTH.update({"Hero_1": 45, "Hero_2": -45, "Hero_3": 50, "Hero_4": -20, "Hero_5": 15})
HERO_POP = {h: 1.0 for h in HEROES}
HERO_POP.update({"Hero_1": 3.0, "Hero_2": 2.6, "Hero_3": 0.35, "Hero_6": 1.8, "Hero_7": 0.5,
                 "Hero_8": 1.6, "Hero_9": 0.6, "Hero_10": 1.3})


def make_players(rng):
    skill = np.clip(rng.normal(2000, 450, N_PLAYERS), 700, 3600)
    mmr = skill + rng.normal(0, 60, N_PLAYERS)
    ptype = np.array(["normal"] * N_PLAYERS, dtype=object)
    idx = rng.permutation(N_PLAYERS)
    cheaters, smurfs, toxic = idx[:15], idx[15:40], idx[40:80]
    ptype[cheaters] = "cheater"
    ptype[smurfs] = "smurf"
    ptype[toxic] = "toxic"
    # Смурф: новый аккаунт с низким MMR, но навык опытного игрока
    mmr[smurfs] = rng.normal(1250, 120, len(smurfs))
    skill[smurfs] = rng.normal(2900, 150, len(smurfs))
    # Активность: часть игроков играет много, часть — мало
    activity = rng.gamma(1.6, 1.0, N_PLAYERS)
    activity[cheaters] = np.maximum(activity[cheaters], 1.5) * 2.0
    return pd.DataFrame({
        "player_id": [f"PLAYER_{i:05d}" for i in range(N_PLAYERS)],
        "true_skill": skill.round(0), "mmr": mmr.round(0), "player_type": ptype,
        "home_region": rng.choice(REGIONS, N_PLAYERS, p=[0.35, 0.25, 0.2, 0.2]),
        "activity": activity,
    })


def main():
    rng = np.random.default_rng(SEED)
    players = make_players(rng)
    mmr = players["mmr"].to_numpy(dtype=float).copy()
    skill = players["true_skill"].to_numpy(dtype=float)
    ptype = players["player_type"].to_numpy()
    act_p = (players["activity"] / players["activity"].sum()).to_numpy()
    hero_w = np.array([HERO_POP[h] for h in HEROES])
    hero_w = hero_w / hero_w.sum()

    matches, stats, reports = [], [], []
    start = pd.Timestamp("2026-09-01")
    for m in range(N_MATCHES):
        mid = f"MATCH_{m:05d}"
        map_id = rng.choice(MAPS, p=MAP_P)
        region = rng.choice(REGIONS, p=[0.35, 0.25, 0.2, 0.2])
        queue = float(np.clip(rng.lognormal(4.2, 0.6), 15, 900))  # секунды
        # Чем дольше поиск, тем уже окно MMR (матчмейкер успевает найти близких по силе)
        window = 650 / (1 + queue / 90) + 60

        anchor = rng.choice(N_PLAYERS, p=act_p)
        near = np.where(np.abs(mmr - mmr[anchor]) <= window)[0]
        near = near[near != anchor]
        if len(near) < 9:
            near = np.argsort(np.abs(mmr - mmr[anchor]))[1:40]
        w = act_p[near] / act_p[near].sum()
        lobby = np.concatenate([[anchor], rng.choice(near, 9, replace=False, p=w)])

        order = lobby[np.argsort(-mmr[lobby])]
        if rng.random() < 0.20:
            # Баг балансировщика: пятёрка сильнейших против пятёрки слабейших
            team_a, team_b = order[:5], order[5:]
            if rng.random() < 0.5:
                team_a, team_b = team_b, team_a
        else:
            # Нормальный «змеиный» драфт: 1-4-5-8-9 против 2-3-6-7-10
            team_a, team_b = order[[0, 3, 4, 7, 8]], order[[1, 2, 5, 6, 9]]
            if rng.random() < 0.5:
                team_a, team_b = team_b, team_a

        heroes_a = rng.choice(HEROES, 5, replace=False, p=hero_w)
        heroes_b = rng.choice(HEROES, 5, replace=False, p=hero_w)

        def power(team, heroes):
            boost = sum(400 if ptype[p] == "cheater" else 0 for p in team)
            return skill[team].mean() + sum(HERO_STRENGTH[h] for h in heroes) + boost

        diff = power(team_a, heroes_a) - power(team_b, heroes_b) + MAP_SIDE_BIAS[map_id]
        p_attack = 1 / (1 + 10 ** (-diff / 400))
        attack_wins = rng.random() < p_attack
        winner = "Attack" if attack_wins else "Defense"
        duration = int(np.clip(rng.normal(1950, 380) - abs(diff) * 0.8, 900, 3300))
        date = start + pd.Timedelta(minutes=int(rng.integers(0, 14 * 24 * 60)))
        matches.append({"match_id": mid, "map_id": map_id, "duration_sec": duration, "server_region": region,
                        "winning_team": winner, "queue_time_sec": round(queue, 1), "match_date": date})

        for side, team, heroes, won in [("Attack", team_a, heroes_a, attack_wins),
                                        ("Defense", team_b, heroes_b, not attack_wins)]:
            opp = team_b if side == "Attack" else team_a
            for p, hero in zip(team, heroes):
                rel = float(np.clip((skill[p] - skill[opp].mean()) / 400, -0.9, 0.9))
                if ptype[p] == "cheater":
                    kills, deaths = int(rng.integers(28, 46)), int(rng.integers(0, 5))
                else:
                    kills = int(rng.poisson(max(2, 15 + 6 * rel + (2 if won else -2))))
                    deaths = int(rng.poisson(max(2, 15 - 6 * rel + (-2 if won else 2))))
                assists = int(rng.poisson(5))
                delta_mmr = int(round(25 * (1 if won else -1) + rng.normal(0, 3)))
                ping = int(max(5, rng.normal(REGION_PING[region], 12)
                               + (40 if players.at[p, "home_region"] != region else 0)))
                stats.append({"match_id": mid, "player_id": players.at[p, "player_id"], "team_side": side,
                              "hero_id": hero, "weapon_id": rng.choice(WEAPONS), "kills": kills, "deaths": deaths,
                              "assists": assists, "mmr_before": int(mmr[p]), "mmr_after": int(mmr[p]) + delta_mmr,
                              "ping": ping})
                mmr[p] += delta_mmr if ptype[p] != "smurf" else delta_mmr * 0.4

            # Жалобы: каждый игрок команды может пожаловаться на соперника
            for reporter in team:
                for target in opp:
                    t = ptype[target]
                    if t == "cheater" and rng.random() < 0.30:
                        reason = "cheating"
                    elif t == "smurf" and rng.random() < 0.06:
                        reason = "cheating"
                    elif t == "toxic" and rng.random() < 0.15:
                        reason = "toxicity"
                    elif rng.random() < 0.006:
                        reason = rng.choice(["cheating", "toxicity", "afk"], p=[0.3, 0.5, 0.2])
                    else:
                        continue
                    reports.append({"match_id": mid, "reporter_id": players.at[reporter, "player_id"],
                                    "reported_id": players.at[target, "player_id"], "reason": reason})

    DATA.mkdir(exist_ok=True)
    matches_df = pd.DataFrame(matches)
    stats_df = pd.DataFrame(stats)
    reports_df = pd.DataFrame(reports)
    matches_df.to_csv(DATA / "matches.csv", index=False)
    stats_df.to_csv(DATA / "player_stats.csv", index=False)
    reports_df.to_csv(DATA / "reports.csv", index=False)
    players[["player_id", "player_type", "true_skill", "home_region"]].to_csv(DATA / "ground_truth.csv", index=False)
    print(f"matches: {len(matches_df)}, player_stats: {len(stats_df)}, reports: {len(reports_df)}")


if __name__ == "__main__":
    main()

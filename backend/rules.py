from dataclasses import dataclass

@dataclass
class Judgement:
    xp_delta: int
    penalty: int
    new_xp: int
    new_level: int
    new_streak: int
    new_debt: int
    new_difficulty: int
    verdict: str

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))

def level_from_xp(xp: int) -> int:
    # Level up every 250 XP (cap 10)
    return clamp(1 + (xp // 250), 1, 10)

def judge_day(
    *,
    current_xp: int,
    current_streak: int,
    current_debt: int,
    current_difficulty: int,
    completed: int,
    missed: int,
    impacts_sum: float
) -> Judgement:
    # Deterministic + brutal:
    # Completed earns XP scaled by impact + current difficulty.
    base_xp = int(round(20 * completed + 10 * impacts_sum + 5 * current_difficulty))
    penalty = 15 * missed

    # Streak
    if missed == 0 and completed > 0:
        new_streak = current_streak + 1
    else:
        new_streak = 0

    # Streak bonus after day 3
    streak_bonus = 5 if new_streak >= 3 else 0

    xp_delta = base_xp + streak_bonus - penalty
    new_xp = max(0, current_xp + xp_delta)

    # Permanent debt from misses
    new_debt = current_debt + missed

    # Difficulty: misses punish you; strong streak also raises the bar.
    diff = current_difficulty
    if missed >= 2:
        diff += 1
    elif new_streak >= 5:
        diff += 1
    elif missed == 0 and completed >= 4:
        diff += 1

    new_difficulty = clamp(diff, 1, 5)
    new_level = level_from_xp(new_xp)

    # Verdict (confrontational, concise)
    if completed == 0 and missed > 0:
        verdict = "You executed nothing. That's self-deception. Penalty applied."
    elif missed == 0 and completed >= 4:
        verdict = "You executed hard. Keep going. Next level demands more."
    elif missed == 0 and completed > 0:
        verdict = "You did the work. No excuses. No detours."
    elif missed >= 2:
        verdict = "You avoided the main goal. You pay now and later. Fix it."
    else:
        verdict = "You did something â€” then you bailed on the rest. Not enough."

    return Judgement(
        xp_delta=xp_delta,
        penalty=penalty,
        new_xp=new_xp,
        new_level=new_level,
        new_streak=new_streak,
        new_debt=new_debt,
        new_difficulty=new_difficulty,
        verdict=verdict,
    )

def evaluate(s, alerts):
    current, prev, low = s["current"], s["previous"], s["low"]
    drop = ((prev-current)/prev*100) if prev and prev > 0 else 0
    score = 0
    if current <= alerts.get("price_threshold", 0): score += 45
    if drop >= alerts.get("drop_percentage", 100): score += 35
    if current <= s["avg7"]: score += 10
    if current <= low: score += 10
    score = min(100, round(score))
    reasons = []
    if current <= alerts.get("price_threshold", 0): reasons.append("below threshold")
    if drop >= alerts.get("drop_percentage", 100): reasons.append(f"drop {drop:.1f}%")
    if score >= alerts.get("deal_score", 101): reasons.append(f"score {score}")
    return {"drop_percentage": drop, "score": score, "should_notify": bool(reasons), "reasons": reasons}


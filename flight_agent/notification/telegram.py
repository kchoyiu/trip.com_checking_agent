import httpx

class TelegramNotifier:
    def __init__(self, token, chat_id): self.token, self.chat_id = token, chat_id
    def send(self, text):
        if not self.token or not self.chat_id: return False
        r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       json={"chat_id": self.chat_id, "text": text}, timeout=20)
        r.raise_for_status()
        return True

def format_deal(job, price, deal):
    dates = str(job["depart_date"])
    if job["return_date"]:
        dates = f"{dates} to {job['return_date']}"
    else:
        dates = f"{dates} (one-way)"
    return (f"🔥 Flight deal {job['origin']} → {job['destination']}\n"
            f"{dates}\n"
            f"Price: {price.currency} {price.price:,.0f}\n"
            f"Airline: {price.airline}\n"
            f"Drop: {deal['drop_percentage']:.1f}% | Score: {deal['score']}/100\n"
            f"Reasons: {', '.join(deal['reasons'])}\n{price.url}")

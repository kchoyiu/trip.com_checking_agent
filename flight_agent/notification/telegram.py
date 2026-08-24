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
    return (f"🔥 Flight deal {job['origin']} → {job['destination']}\n"
            f"{job['depart_date']} to {job['return_date']}\n"
            f"Price: {price.currency} {price.price:,.0f}\n"
            f"Airline: {price.airline}\n"
            f"Drop: {deal['drop_percentage']:.1f}% | Score: {deal['score']}/100\n"
            f"Reasons: {', '.join(deal['reasons'])}\n{price.url}")

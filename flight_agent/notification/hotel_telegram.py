from .telegram import TelegramNotifier

def format_hotel_drop(city, row, previous, drop):
    return (
        f"🏨 Hotel price drop: {city}\n"
        f"{row.hotel_name}\n"
        f"Current: {row.price} (previous {row.currency} {previous:,.0f})\n"
        f"Drop: {drop:.1f}% | Score: {row.score:.1f}/100\n"
        f"{row.source_url}"
    )

def notify_top_drop(city, row, previous, drop, token, chat_id):
    return TelegramNotifier(token, chat_id).send(format_hotel_drop(city, row, previous, drop))


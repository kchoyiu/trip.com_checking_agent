from .telegram import TelegramNotifier

def format_hotel_prices(city, rows, previous, drop_threshold=10):
    lines = [f"🏨 Hotel price update: {city}"]
    for index, row in enumerate(rows, start=1):
        old_price = previous.get(row.hotel_name)
        if old_price and row.price_value:
            drop = (old_price - row.price_value) / old_price * 100
            if drop > 0:
                change = f"drop: {drop:.1f}%"
            elif drop < 0:
                change = f"increase: {abs(drop):.1f}%"
            else:
                change = "change: 0.0%"
            marker = " 🔥" if drop >= drop_threshold else ""
        else:
            change = "change: baseline"
            marker = ""
        lines.extend([
            f"{index}. {row.hotel_name}{marker}",
            f"Current: {row.price}",
            f"Score: {row.score:.1f}/100 | {change}",
        ])
        if row.source_url:
            lines.append(row.source_url)
    return "\n".join(lines)

def notify_current_prices(city, rows, previous, token, chat_id, drop_threshold=10):
    message = format_hotel_prices(city, rows, previous, drop_threshold)
    return TelegramNotifier(token, chat_id).send(message)

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

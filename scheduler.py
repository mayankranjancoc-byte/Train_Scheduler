from models import Train
from tried2 import app, db

def get_train_schedule():
    """Return trains sorted by priority and ID (as a proxy for departure order)."""

    with app.app_context():
        trains = Train.query.all()

        # Sort trains by priority (desc) then by ID (asc, as a simple stand-in for departure time)
        sorted_trains = sorted(trains, key=lambda t: (-t.priority, t.id))

        schedule = []
        for t in sorted_trains:
            schedule.append({
                "id": t.id,
                "name": t.name,
                "type": t.train_type,
                "priority": t.priority,
                "route": t.route.name if t.route else "N/A"
            })
        return schedule

if __name__ == "__main__":
    schedule = get_train_schedule()
    print("🚉 Train Schedule (precedence order):")
    for s in schedule:
        print(f"{s['name']} ({s['type']}, priority {s['priority']}) → {s['route']}")

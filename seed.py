from tried2 import app, db
from models import Station, Platform, Train, Route

with app.app_context():
    # Clear old data
    db.drop_all()
    db.create_all()

    # Create stations
    station_a = Station(name="Station A", location="City A")
    station_b = Station(name="Station B", location="City B")
    station_c = Station(name="Station C", location="City C")

    db.session.add_all([station_a, station_b, station_c])
    db.session.commit()

    # Add platforms
    platform_a1 = Platform(number=1, station=station_a)
    platform_a2 = Platform(number=2, station=station_a)
    platform_b1 = Platform(number=1, station=station_b)
    platform_c1 = Platform(number=1, station=station_c)

    db.session.add_all([platform_a1, platform_a2, platform_b1, platform_c1])
    db.session.commit()

    # Create routes
    route_ab = Route(name="Route A-B")
    route_ac = Route(name="Route A-C")

    db.session.add_all([route_ab, route_ac])
    db.session.commit()

    # Add trains
    train1 = Train(name="Local 101", train_type="local", priority=1, route=route_ab)
    train2 = Train(name="Express 201", train_type="express", priority=3, route=route_ab)
    train3 = Train(name="Freight 301", train_type="freight", priority=2, route=route_ac)

    db.session.add_all([train1, train2, train3])
    db.session.commit()

    print("✅ Database seeded with sample data!")
